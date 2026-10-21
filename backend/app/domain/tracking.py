"""Pure tracking lifecycle decisions, shared by services and reproducible tests."""
from app.domain.conditions import evaluate

def trading_age(calendar,entry_date,observed_date):
    if entry_date not in calendar or observed_date not in calendar:return None
    return calendar.index(observed_date)-calendar.index(entry_date)

def condition_decisions(policy,metrics,age,history=None):
    def group(conditions,logic):
        return evaluate({'all' if logic=='AND' else 'any':conditions},metrics,history) if conditions else {'result':False,'checks':[],'logic':logic}
    alert=group(policy['alerts'],policy['alert_logic'])
    exit_=group(policy['exits'],policy['exit_logic'])
    if age==0 and not policy['allow_entry_day_alerts']:alert={'result':False,'checks':[],'reason':'D0提醒未启用'}
    if age==0 and not policy['allow_entry_day_exits']:exit_={'result':False,'checks':[],'reason':'D0结束未启用'}
    return alert,exit_

def terminal_status(exit_result,age,duration_sessions):
    if exit_result is True:return 'condition_ended'
    if age is not None and age>0 and age>=duration_sessions:return 'expired'
    return None
