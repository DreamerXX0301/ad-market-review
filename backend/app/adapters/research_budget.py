"""One manual research's shared public HTTP budget; no background scheduling."""
import time
from app.adapters.providers import ProviderError


class ResearchPublicBudget:
    def __init__(self, limits, *, clock=time.monotonic, checkpoint=None):
        self.limits = dict(limits)
        self.clock = clock
        self.checkpoint = checkpoint
        self.started = clock()
        self.attempts = 0
        self.blocked = set()

    def before_attempt(self, source):
        if self.checkpoint: self.checkpoint()
        if source in self.blocked:
            raise ProviderError('该公开来源已拒绝访问或限流，本任务不再请求该来源')
        if self.attempts >= self.limits['public_max_http_attempts']:
            raise ProviderError('本次公开材料请求预算已用完；剩余材料保留缺口')
        remaining = self.limits['public_deadline_seconds']-(self.clock()-self.started)
        if remaining <= 0:
            raise ProviderError('本次公开材料查询已达到总时间预算；剩余材料保留缺口')
        self.attempts += 1
        return min(self.limits['public_timeout_seconds'], remaining)

    def refuse(self, source):
        self.blocked.add(source)

    def check_response(self, byte_count):
        """Check every received chunk, before retaining it or requesting another."""
        if self.checkpoint: self.checkpoint()
        if byte_count > self.limits['public_max_response_bytes']:
            raise ProviderError('公开研究来源单页超过冻结容量边界')
        if self.clock()-self.started >= self.limits['public_deadline_seconds']:
            raise ProviderError('本次公开材料查询已达到总时间预算；已停止读取来源响应')

    def view(self):
        return {'http_attempts': self.attempts, 'max_http_attempts': self.limits['public_max_http_attempts'],
                'deadline_seconds': self.limits['public_deadline_seconds'],
                'elapsed_ms': max(0, round((self.clock()-self.started)*1000)),
                'blocked_sources': sorted(self.blocked), 'scope': 'public_research_http_only'}
