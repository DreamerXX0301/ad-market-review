"""Single-worker AI task coordinator with one cancellable network-only IO thread.

Only tick/recover on the existing worker thread use SQLite. The executor receives
an immutable bounded request and decrypted credential, and performs HTTP only.
"""
from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time
from pydantic import ValidationError
from app.core import connect, now_ms, new_id, dumps
from app.infrastructure.credentials import CredentialStore
from app.adapters.ai_responses import perform, ProviderError
from app.services import ai_service as service
from app.services import ai_web
from app.services.ai_catalog import estimated_nusd, request_max_output
from app.services.ai_validation import validate_response
from app.services.ai_agent_runtime import AgentRuntimeMixin
from app.adapters.ai_agent import perform_agent

class AIRuntime(AgentRuntimeMixin):
    def __init__(self, transport=None, agent_transport=None):
        self.transport = transport or perform
        self.agent_transport = agent_transport or perform_agent
        self.agent_bridge=None
        self.agent_state=None
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='ai-http')
        self.active = None
        self.closed = False
        self.last_cleanup = 0

    def recover_interrupted(self):
        """Called once by the unique worker after obtaining its process lock."""
        with connect() as db:
            self._recover_agents(db)
            # Sent attempts are never re-issued, even if there is no HTTP receipt.
            db.execute("UPDATE ai_task SET state='interrupted_manual_retry',reason_code='interrupted_manual_retry',finished_at_ms=?,cost_state='pending',request_json='{}',credential_ref='' WHERE state='running' AND sent_at_ms IS NOT NULL", (now_ms(),))
            db.execute("UPDATE ai_task SET state='queued',owner=NULL,started_at_ms=NULL WHERE state='running' AND sent_at_ms IS NULL AND cancel_requested=0")
            db.execute("UPDATE ai_task SET state='cancelled',reason_code='cancelled',finished_at_ms=? WHERE state='running' AND sent_at_ms IS NULL AND cancel_requested=1", (now_ms(),))
            # A verifying spinner must not outlive a terminal interrupted probe.
            for row in db.execute("SELECT provider_id,verification_json FROM ai_provider_config"):
                verification = json.loads(row['verification_json'])
                task = db.execute('SELECT state FROM ai_task WHERE task_id=?', (verification.get('task_id', ''),)).fetchone()
                if verification.get('state') == 'verifying' and task and task['state'] not in ('queued','running'):
                    verification.update(state='unverified', reason_code='interrupted_manual_retry')
                    db.execute('UPDATE ai_provider_config SET verification_json=? WHERE provider_id=?', (dumps(verification), row['provider_id']))

    def tick(self, owner, allow_start=True, *, allow_cleanup=True):
        """Drain completion first, then (optionally) claim one low-priority task."""
        if self.closed: return
        if self.active:
            task_id, future, cancel = self.active
            if self.agent_bridge is not None:self._drain_agent(task_id)
            if future.done():
                if self.agent_bridge is not None:self._finish_agent(task_id,future)
                else:self._finish(task_id, future)
                self.active = None
            else:
                with connect() as db:
                    task = db.execute('SELECT * FROM ai_task WHERE task_id=?', (task_id,)).fetchone()
                    row = service._provider(db, task['provider_id']) if task else None
                    limits = service._limits(db)
                if not task or task['cancel_requested'] or service.config_signature(row, limits) != task['config_signature'] or (task['operation'] == 'message' and not row['enabled']):
                    cancel.set()
                return
        if allow_cleanup and now_ms() - self.last_cleanup > 300000:
            with connect() as db:
                service._purge_expired(db)
                self._cleanup_agent_events(db)
            self._cleanup_credentials()
            self.last_cleanup = now_ms()
        if not allow_start: return
        claimed = self._claim(owner)
        if claimed is None: return
        task, request = claimed
        try:
            api_key = CredentialStore().get(task['credential_ref'])
            if not api_key: raise ValueError()
        except (ValueError, OSError):
            self._fail_unsent(task, 'credential_unavailable')
            return
        if request.get('agent'):
            self._start_agent(task,request,api_key)
            return
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            latest = db.execute('SELECT cancel_requested FROM ai_task WHERE task_id=?', (task['task_id'],)).fetchone()
            row, limits = service._provider(db, task['provider_id']), service._limits(db)
            reason = ('cancelled' if latest['cancel_requested'] else 'configuration_changed'
                      if service.config_signature(row, limits) != task['config_signature'] or (task['operation']=='message' and not row['enabled']) else None)
            if reason:
                db.execute("UPDATE ai_task SET state=?,reason_code=?,finished_at_ms=?,reservation_nusd=0,cost_state='not_sent',request_json='{}',credential_ref='' WHERE task_id=?", ('cancelled' if reason=='cancelled' else 'failed',reason,now_ms(),task['task_id']))
                return
            # Conservative transmission audit: a crash immediately after this
            # commit requires manual review rather than risking a duplicate charge.
            db.execute("UPDATE ai_task SET sent_at_ms=?,cost_state='pending' WHERE task_id=?", (now_ms(), task['task_id']))
        cancel = threading.Event()
        future = self.executor.submit(self.transport, request, api_key, cancel)
        self.active = task['task_id'], future, cancel

    def _claim(self, owner):
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM ai_task WHERE state='running'").fetchone(): return None
            raw = db.execute("SELECT * FROM ai_task WHERE state='queued' ORDER BY created_at_ms,rowid LIMIT 1").fetchone()
            if raw is None: return None
            task = dict(raw)
            row, limits = service._provider(db, task['provider_id']), service._limits(db)
            reason = None
            if service.config_signature(row, limits) != task['config_signature'] or (task['operation'] == 'message' and (not row['enabled'] or service._verification(row, limits)['state'] != 'passed')):
                reason = 'configuration_changed'
            if task['operation'] == 'message' and not task['session_id']: reason = 'session_deleted'
            if task['session_id']:
                session = db.execute('SELECT keep,expires_at_ms FROM ai_session WHERE session_id=?', (task['session_id'],)).fetchone()
                if session is None or (not session['keep'] and session['expires_at_ms'] <= now_ms()): reason = 'session_deleted'
            request = json.loads(task['request_json'])
            input_bytes = service._input_size(task, request, task['operation'])
            if input_bytes > service.input_limit(request): reason = 'invalid_output'
            # A UTF-8 byte is a conservative token upper bound for these bounded
            # prompts. Tokenizer differences cannot bypass the preflight reserve.
            try:
                receipt=json.loads(task['result_json'] or '{}')
                reservation = estimated_nusd(task['provider_id'], task['model'], input_bytes, request_max_output(task['provider_id'],request,probe=task['operation']=='verify'), rates=receipt.get('price_rates'))
                if request.get('allow_web'):
                    if not ai_web.supported(task['provider_id'],task['model']): raise ValueError('web_search_unavailable')
                    reservation += ai_web.extra_reservation_nusd(receipt['price_rates'],receipt['web_policy'])
            except ValueError:
                reservation, reason = 0, 'unknown_price'
            day = service.day_key()
            used=service.budget_state(db,day,include_queued=False)
            if used['requests'] >= limits['daily_request_limit'] or used['amount']+reservation > limits['daily_budget_nusd']:
                reason = 'budget_exceeded'
            if reason:
                db.execute("UPDATE ai_task SET state='failed',reason_code=?,finished_at_ms=?,request_json='{}',credential_ref='' WHERE task_id=?", (reason,now_ms(),task['task_id']))
                self._probe_failure(db, task, row, limits, reason)
                return None
            db.execute("UPDATE ai_task SET state='running',owner=?,started_at_ms=?,day=?,reservation_nusd=? WHERE task_id=?", (owner,now_ms(),day,reservation,task['task_id']))
            request.update(provider_id=task['provider_id'], model=task['model'], operation=task['operation'], timeout_seconds=limits['timeout_seconds'])
            return task, request

    def _fail_unsent(self, task, reason):
        with connect() as db:
            db.execute("UPDATE ai_task SET state='failed',reason_code=?,finished_at_ms=?,reservation_nusd=0,cost_state='not_sent',request_json='{}',credential_ref='' WHERE task_id=?", (reason,now_ms(),task['task_id']))
            self._probe_failure(db, task, service._provider(db, task['provider_id']), service._limits(db), reason)

    def _probe_failure(self, db, task, row, limits, reason):
        if task['operation'] != 'verify' or service.config_signature(row, limits) != task['config_signature']: return
        old = json.loads(row['verification_json'])
        if old.get('task_id') != task['task_id']: return
        state = 'temporary_error' if reason in ('network_error','timeout','provider_unavailable','rate_limited') else 'failed'
        result = {'state': state, 'items': [{'capability': 'authentication', 'state': 'failed' if reason=='authentication_failed' else 'unknown'},
                                          {'capability': 'model_request', 'state': 'failed', 'reason_code': reason},
                                          {'capability': 'structured_output', 'state': 'unknown'}],
                  'checked_at_ms': now_ms(), 'config_signature': task['config_signature'], 'task_id': task['task_id'], 'reason_code': reason}
        db.execute('UPDATE ai_provider_config SET verification_json=? WHERE provider_id=?', (dumps(result), task['provider_id']))

    def _finish(self, identifier, future):
        reason, response = None, {}
        try: response = future.result()
        except ProviderError as error:
            reason, response = error.reason_code, {'usage': error.usage, 'web_accounting': error.web_accounting}
        except Exception: reason = 'internal_error'
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            raw = db.execute('SELECT * FROM ai_task WHERE task_id=?', (identifier,)).fetchone()
            if raw is None: return
            task = dict(raw)
            if task['state'] != 'running': return
            row, limits = service._provider(db, task['provider_id']), service._limits(db)
            if task['cancel_requested']: reason = 'configuration_changed' if task['reason_code']=='configuration_changed' else 'cancelled'
            elif service.config_signature(row, limits) != task['config_signature'] or (task['operation']=='message' and not row['enabled']):
                reason = 'configuration_changed'
            usage = response.get('usage') or {}
            amount, cost_state = None, 'pending'
            if (all(isinstance(usage.get(key), int) and not isinstance(usage.get(key), bool) and 0<=usage[key]<=10000000 for key in ('input_tokens','output_tokens'))
                    and 0 <= (usage.get('cached_tokens') or 0)+(usage.get('cache_write_tokens') or 0) <= usage['input_tokens']
                    and 0 <= (usage.get('reasoning_tokens') or 0) <= usage['output_tokens']):
                amount = estimated_nusd(task['provider_id'], task['model'], usage['input_tokens'], usage['output_tokens'], usage.get('cached_tokens') or 0, usage.get('cache_write_tokens') or 0, rates=json.loads(task['result_json'] or '{}').get('price_rates'))
                cost_state = 'estimated'
            receipt=json.loads(task['result_json'] or '{}')
            if receipt.get('web_policy'):
                accounting=response.get('web_accounting') or {}
                count=accounting.get('search_call_count')
                receipt['web_accounting']={key:accounting.get(key) for key in ('search_call_count','used','complete','search_content_in_input')}
                if amount is not None:
                    receipt['known_base_cost_nusd']=amount
                    if accounting.get('complete') is True and type(count) is int and 0<=count<=1:
                        receipt['known_tool_cost_nusd']=count*receipt['web_policy']['tool_nusd']
                        if accounting.get('used') is False or accounting.get('search_content_in_input') is True:
                            amount += receipt['known_tool_cost_nusd']
                        else:
                            amount, cost_state = None, 'pending'
                    else:
                        amount, cost_state = None, 'pending'
            output = response.get('output')
            web_output = False
            if reason is None:
                try:
                    if task['operation'] == 'verify':
                        if output != {'probe': 'ok', 'missing': None}: raise ValueError('invalid_output')
                    else:
                        session = db.execute('SELECT * FROM ai_session WHERE session_id=?', (task['session_id'],)).fetchone()
                        if session is None: raise ValueError('session_deleted')
                        protocol=json.loads(session['protocol_json'])
                        if protocol.get('allow_web'):
                            if (not isinstance(output,dict) or output.get('response_type')!='web_answer'
                                    or output.get('web',{}).get('requested_as_of')!=session['as_of']):
                                raise ValueError('invalid_output')
                            web_output=True
                        else:
                            output = validate_response(output, json.loads(session['context_json']), protocol)
                except ValidationError: reason = 'invalid_output'
                except ValueError as error: reason = str(error) if str(error) in service.MESSAGES else 'invalid_output'
                except Exception: reason = 'invalid_output'
            state = 'completed' if reason is None else 'cancelled' if reason=='cancelled' else 'failed'
            db.execute("UPDATE ai_task SET state=?,reason_code=?,finished_at_ms=?,usage_json=?,cost_nusd=?,cost_state=?,response_model=?,result_json=?,request_json='{}',credential_ref='' WHERE task_id=?", (state,reason,now_ms(),dumps(usage),amount,cost_state,response.get('response_model'),dumps(receipt),identifier))
            if task['operation'] == 'verify':
                if reason: self._probe_failure(db, task, row, limits, reason)
                else:
                    old = json.loads(row['verification_json'])
                    # A newer probe owns the UI state; an older result is only history.
                    if old.get('task_id') == identifier:
                        verification = {'state': 'passed', 'items': [{'capability': name, 'state': 'passed'} for name in ('authentication','model_request','structured_output')],
                                        'checked_at_ms': now_ms(), 'config_signature': task['config_signature'], 'task_id': identifier,
                                        'response_model': response.get('response_model')}
                        db.execute('UPDATE ai_provider_config SET verification_json=?,models_json=? WHERE provider_id=?', (dumps(verification),dumps(response.get('models') or []),task['provider_id']))
            elif task['session_id']:
                stamp = now_ms()
                # Completion is explicit activity; merely browsing never extends TTL.
                db.execute('INSERT INTO ai_message VALUES(?,?,?,?,?,?,?,?)', (new_id('ai-message'),task['session_id'],identifier,'assistant',(output['text'] if web_output else dumps(output)) if reason is None else service.MESSAGES.get(reason,'本次请求未完成'),dumps(output) if reason is None else None,state,stamp))
                db.execute('UPDATE ai_session SET updated_at_ms=?,expires_at_ms=? WHERE session_id=?', (stamp,stamp+service.SESSION_TTL,task['session_id']))

    def _cleanup_credentials(self):
        store = CredentialStore()
        if not store.directory.exists(): return
        with connect() as db:
            referenced = {row[0] for row in db.execute('SELECT credential_ref FROM ai_provider_config WHERE credential_ref IS NOT NULL')}
            referenced.update(row[0] for row in db.execute("SELECT credential_ref FROM ai_task WHERE state IN ('queued','running')"))
        for path in list(store.directory.glob('ai-*.enc'))[:100]:
            if path.stem not in referenced and time.time()-path.stat().st_mtime > 300:
                path.unlink(missing_ok=True)

    def shutdown(self):
        """Bounded cooperative shutdown; uncertain sent receipts remain pending."""
        self.closed = True
        if self.active:
            identifier, future, cancel = self.active
            cancel.set()
            try: future.result(timeout=2)
            except Exception: pass
            if self.agent_bridge is not None:self._drain_agent(identifier)
            if future.done():
                if self.agent_bridge is not None:self._finish_agent(identifier,future)
                else:self._finish(identifier, future)
        self.executor.shutdown(wait=False, cancel_futures=True)
