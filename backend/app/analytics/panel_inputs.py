"""Bounded array inputs for dated panels, sharing the scalar indicator semantics.

Only historical rows through the requested date are evaluated. This avoids
constructing a million Python dictionaries for each market date; charts and
strategies still use the same indicator definitions and reset boundaries.
"""
from __future__ import annotations

from collections import OrderedDict

import numpy as np
import pandas as pd

from .indicators import WINDOWS, INTRADAY_HIGH_REASONS, INTRADAY_HIGH_DECIMALS


class PanelInputs:
    CHECKPOINT_INTERVAL = 64
    MAX_CHECKPOINTS = 8

    def __init__(self, frame, calendar):
        self.calendar = list(calendar)
        self.positions = {day: i for i, day in enumerate(self.calendar)}
        if isinstance(frame.instrument_id.dtype, pd.ArrowDtype):
            import pyarrow as pa
            import pyarrow.compute as pc
            column = frame.instrument_id.array.__arrow_array__()
            unique = pc.unique(column)
            if unique.null_count:raise ValueError('行情证券代码为空，拒绝生成指标')
            self.symbols = sorted(unique.to_pylist())
            encoded = pc.index_in(column, value_set=pa.array(self.symbols, type=column.type))
            if encoded.null_count:raise ValueError('行情证券编码失败')
            columns = encoded.to_numpy(zero_copy_only=False)
        else:
            self.symbols = sorted(frame.instrument_id.unique())
            columns = pd.Categorical(frame.instrument_id, categories=self.symbols).codes
        self.symbol_index = {symbol: i for i, symbol in enumerate(self.symbols)}
        if isinstance(frame.date.dtype, pd.ArrowDtype):
            import pyarrow as pa
            import pyarrow.compute as pc
            column = frame.date.array.__arrow_array__()
            encoded = pc.index_in(column, value_set=pa.array(self.calendar, type=column.type))
            if encoded.null_count:raise ValueError('行情包含交易日历以外的日期，拒绝生成跨日期指标')
            days = encoded.to_numpy(zero_copy_only=False)
        else:
            days = frame.date.map(self.positions)
            if days.isna().any():
                raise ValueError('行情包含交易日历以外的日期，拒绝生成跨日期指标')
            days = days.to_numpy(dtype=np.int64)
        shape = (len(self.calendar), len(self.symbols))
        self.values = {}
        for field in ('close', 'high', 'low', 'volume'):
            matrix = np.full(shape, np.nan)
            matrix[days, columns] = frame[field].to_numpy(dtype=float)
            self.values[field] = matrix
        self.actions = np.zeros(shape, dtype=bool)
        self.actions[days, columns] = frame.corporate_action.to_numpy(dtype=bool)
        self.action_coverage = True
        if 'corporate_action_coverage_known' in frame:
            covered = frame.corporate_action_coverage_known.eq(True).fillna(False).to_numpy(dtype=bool)
            if not covered.any():self.action_coverage = False
            elif not covered.all():
                self.action_coverage = np.zeros(shape, dtype=bool)
                self.action_coverage[days, columns] = covered
        # A checkpoint is state *after* its own day. Restore only an earlier day,
        # then recompute the requested day so crossings keep their prior values.
        self._checkpoints = OrderedDict()
        self._history_inputs = None
        self._history_valid = None
        self.last_compute_steps = 0
        self.last_checkpoint_position = None

    def history_view(self, frame):
        """Share price arrays, but keep stricter historical-event state separate.

        A zero-volume, malformed, duplicated or action-uncovered daily quote
        cannot seed a historical event indicator.  The ordinary dated panel
        retains its existing semantics and checkpoints; this small fork owns
        only a validity bitmap and its bounded recurrence checkpoints.
        """
        if self._history_inputs is not None:
            return self._history_inputs
        import pyarrow as pa
        import pyarrow.compute as pc
        if isinstance(frame.date.dtype, pd.ArrowDtype):
            column = frame.date.array.__arrow_array__()
            days = pc.index_in(column, value_set=pa.array(self.calendar, type=column.type)).to_numpy(zero_copy_only=False)
        else:
            days = frame.date.map(self.positions).to_numpy(dtype=np.int64)
        if isinstance(frame.instrument_id.dtype, pd.ArrowDtype):
            column = frame.instrument_id.array.__arrow_array__()
            columns = pc.index_in(column, value_set=pa.array(self.symbols, type=column.type)).to_numpy(zero_copy_only=False)
        else:
            columns = pd.Categorical(frame.instrument_id, categories=self.symbols).codes
        close, high, low, volume = (self.values[key] for key in ('close', 'high', 'low', 'volume'))
        opening = frame.open.to_numpy(dtype=float)
        row_high = frame.high.to_numpy(dtype=float)
        row_low = frame.low.to_numpy(dtype=float)
        valid_open = np.isfinite(opening) & (opening > 0) & (opening <= row_high) & (opening >= row_low)
        valid = np.zeros(close.shape, dtype=bool)
        valid[days, columns] = valid_open
        counts = np.zeros(close.shape, dtype=np.uint32)
        np.add.at(counts, (days, columns), 1)
        valid &= counts == 1
        valid &= np.isfinite(close) & (close > 0) & np.isfinite(high) & (high > 0)
        valid &= np.isfinite(low) & (low > 0) & (high >= close) & (low <= close) & (high >= low)
        valid &= np.isfinite(volume) & (volume > 0)
        fork = object.__new__(PanelInputs)
        fork.__dict__ = dict(self.__dict__)
        fork._checkpoints = OrderedDict()
        # Keep a 30-session query's seed checkpoint for a repeated run without
        # increasing the ordinary panel's eight-checkpoint cache.
        fork.MAX_CHECKPOINTS = 64
        fork._history_inputs = None
        fork._history_valid = valid
        self._history_inputs = fork
        return fork

    def _initial_state(self):
        size = len(self.symbols)
        return {
            'count': np.zeros(size, dtype=np.int64),
            'previous': np.full(size, -1, dtype=np.int64),
            'previous_close': np.full(size, np.nan),
            'previous_volume': np.full(size, np.nan),
            'previous_action_coverage': np.zeros(size, dtype=bool),
            'sums': {n: np.zeros(size) for n in WINDOWS},
            'e12': np.full(size, np.nan), 'e26': np.full(size, np.nan),
            'dea': np.full(size, np.nan), 'k': np.full(size, 50.0),
            'd': np.full(size, 50.0),
            'warmup': np.full(size, '初始预热：MACD/KDJ需要35根可比报价', dtype=object),
        }

    @staticmethod
    def _copy_state(state):
        # Warmup elements are immutable strings/None; copying the object array
        # isolates its slots as well as every numeric accumulator.
        return {key: {n: total.copy() for n, total in value.items()} if key == 'sums' else value.copy()
                for key, value in state.items()}

    def _save_checkpoint(self, position, state):
        self._checkpoints[position] = self._copy_state(state)
        self._checkpoints.move_to_end(position)
        while len(self._checkpoints) > self.MAX_CHECKPOINTS:
            self._checkpoints.popitem(last=False)

    def latest(self, day, requested_fields=None, arrays=False):
        end = self.positions[day]
        size = len(self.symbols)
        close, high, low, volume = (self.values[k] for k in ('close', 'high', 'low', 'volume'))
        earlier = [position for position in self._checkpoints if position < end]
        checkpoint = max(earlier) if earlier else None
        if checkpoint is None:
            state = self._initial_state()
            start = 0
        else:
            state = self._copy_state(self._checkpoints[checkpoint])
            self._checkpoints.move_to_end(checkpoint)
            start = checkpoint + 1
        self.last_checkpoint_position = checkpoint
        self.last_compute_steps = 0
        count = state['count']; previous = state['previous']
        previous_close = state['previous_close']; previous_volume = state['previous_volume']
        previous_action_coverage = state['previous_action_coverage']
        sums = state['sums']; e12 = state['e12']; e26 = state['e26']; dea = state['dea']
        k = state['k']; d = state['d']; warmup = state['warmup']
        for position in range(start, end + 1):
            self.last_compute_steps += 1
            current = close[position]
            present = np.isfinite(current)
            if self._history_valid is not None:
                current_coverage = self.action_coverage if isinstance(self.action_coverage, bool) else self.action_coverage[position]
                present &= self._history_valid[position] & current_coverage
            gap = present & (previous >= 0) & (position - previous != 1)
            action = present & self.actions[position]
            reset = gap | action
            missing = np.where(gap, position - previous - 1, 0)
            reasons = np.full(size, None, dtype=object)
            for number in np.unique(missing[gap & ~action]):
                reasons[gap & ~action & (missing == number)] = f'缺少{number}个市场交易日报价，尚未区分停牌与缺数'
            reasons[action] = '公司行动尚未复权，技术指标重新预热'
            warmup[reset] = reasons[reset]
            count[reset] = 0
            e12[reset] = np.nan; e26[reset] = np.nan; dea[reset] = np.nan
            k[reset] = 50; d[reset] = 50
            for total in sums.values(): total[reset] = 0
            count[present] += 1
            fields = {}
            for n in WINDOWS:
                sums[n][present] += current[present]
                remove = present & (count > n)
                if position >= n: sums[n][remove] -= close[position - n, remove]
                fields[f'ma{n}'] = np.where(present & (count >= n), sums[n] / n, np.nan)
            comparable = present & np.isfinite(previous_close) & ~reset
            change = np.full(size, np.nan); ratio = change.copy()
            change[comparable] = (current[comparable] / previous_close[comparable] - 1) * 100
            has_volume = comparable & (previous_volume > 0)
            ratio[has_volume] = volume[position, has_volume] / previous_volume[has_volume]
            returns = np.full(size, np.nan)
            if position >= 30:
                eligible = present & (count >= 31)
                returns[eligible] = (current[eligible] / close[position - 30, eligible] - 1) * 100
            old_dif = e12 - e26; old_dea = dea.copy()
            initial = present & np.isnan(e12)
            following = present & ~initial
            e12[initial] = current[initial]; e26[initial] = current[initial]; dea[initial] = 0
            e12[following] = current[following] * 2 / 13 + e12[following] * 11 / 13
            e26[following] = current[following] * 2 / 27 + e26[following] * 25 / 27
            dif = e12 - e26
            dea[following] = .2 * dif[following] + .8 * dea[following]
            old_k = k.copy(); old_d = d.copy()
            dead_volume = (np.isfinite(volume[position]) & (volume[position] > 0)
                           & np.isfinite(previous_volume) & (previous_volume > 0))
            current_coverage = self.action_coverage if isinstance(self.action_coverage, bool) else self.action_coverage[position]
            high_change = np.full(size, np.nan)
            high_reasons = np.full(size, INTRADAY_HIGH_REASONS['previous'], dtype=object)
            high_ready = present & (previous == position-1) & (previous >= 0)
            high_reasons[high_ready] = INTRADAY_HIGH_REASONS['coverage']
            high_ready &= current_coverage & previous_action_coverage
            high_reasons[high_ready] = INTRADAY_HIGH_REASONS['action']
            high_ready &= ~action
            high_reasons[high_ready] = INTRADAY_HIGH_REASONS['price']
            high_ready &= np.isfinite(high[position]) & (high[position] > 0) & (current > 0) & (previous_close > 0) & (high[position] >= current)
            high_reasons[high_ready] = INTRADAY_HIGH_REASONS['volume']
            high_ready &= dead_volume
            with np.errstate(over='ignore',invalid='ignore'):
                high_change[high_ready] = np.round((high[position, high_ready]/previous_close[high_ready]-1)*100, INTRADAY_HIGH_DECIMALS)
            high_reasons[high_ready] = None
            high_reasons[high_ready & ~np.isfinite(high_change)] = INTRADAY_HIGH_REASONS['price']
            fields['intraday_high_change_pct'] = high_change
            eligible = present & (count >= 9)
            if position >= 8 and eligible.any():
                lows = np.min(low[position - 8:position + 1, eligible], axis=0)
                highs = np.max(high[position - 8:position + 1, eligible], axis=0)
                width = highs - lows
                rsv = np.full(len(width), 50.0)
                np.divide(current[eligible] - lows, width, out=rsv, where=width > 0)
                rsv[width > 0] *= 100
                k[eligible] = 2 * k[eligible] / 3 + rsv / 3
                d[eligible] = 2 * d[eligible] / 3 + k[eligible] / 3
            previous[present] = position
            previous_close[present] = current[present]
            previous_volume[present] = volume[position, present]
            previous_action_coverage[present] = current_coverage if isinstance(current_coverage, bool) else current_coverage[present]
            if (position + 1) % self.CHECKPOINT_INTERVAL == 0 or position == end:
                self._save_checkpoint(position, state)
        ready = present & (count >= 35)
        crosses = present & (count >= 36)
        fields.update(change_pct=change, pct_change=change, volume_ratio=ratio,
                      volume_multiple_prev=ratio, return_30d=returns,
                      macd_dif=np.where(ready, dif, np.nan), macd_dea=np.where(ready, dea, np.nan),
                      macd_hist=np.where(ready, 2 * (dif - dea), np.nan),
                      kdj_k=np.where(ready, k, np.nan), kdj_d=np.where(ready, d, np.nan),
                      kdj_j=np.where(ready, 3 * k - 2 * d, np.nan))
        if arrays:
            # Numeric arrays keep NaN internally; nullable scalars are created
            # only for one bounded security batch, never the whole market.
            fields.update(
                macd_cross=np.where(crosses, (old_dif <= old_dea) & (dif > dea), None),
                kd_cross=np.where(crosses, (old_k <= old_d) & (k > d), None),
                macd_dead_cross=np.where(crosses & dead_volume & np.isfinite(old_dif) & np.isfinite(old_dea) & np.isfinite(dif) & np.isfinite(dea), (old_dif >= old_dea) & (dif < dea), None),
                kd_dead_cross=np.where(crosses & dead_volume & np.isfinite(old_k) & np.isfinite(old_d) & np.isfinite(k) & np.isfinite(d), (old_k >= old_d) & (k < d), None),
                price_continuity=~reset, indicator_warmup_bars=count,
                indicator_reset_reason=reasons,
                indicator_warmup_reason=np.where(count < 35, warmup, None),
                missing_market_sessions_before=missing,
                intraday_high_change_pct_reason=high_reasons,
            )
            selected = fields if requested_fields is None else {key: value for key, value in fields.items() if key in requested_fields}
            return {'present': present, **selected}
        result = {}
        for i in np.flatnonzero(present):
            values = {key: float(value[i]) if np.isfinite(value[i]) else None for key, value in fields.items()}
            values.update(macd_cross=bool(old_dif[i] <= old_dea[i] and dif[i] > dea[i]) if crosses[i] else None,
                          kd_cross=bool(old_k[i] <= old_d[i] and k[i] > d[i]) if crosses[i] else None,
                          macd_dead_cross=bool(old_dif[i] >= old_dea[i] and dif[i] < dea[i]) if crosses[i] and dead_volume[i] and all(np.isfinite(value) for value in (old_dif[i], old_dea[i], dif[i], dea[i])) else None,
                          kd_dead_cross=bool(old_k[i] >= old_d[i] and k[i] < d[i]) if crosses[i] and dead_volume[i] and all(np.isfinite(value) for value in (old_k[i], old_d[i], k[i], d[i])) else None,
                          price_continuity=not bool(reset[i]), indicator_warmup_bars=int(count[i]),
                          indicator_reset_reason=reasons[i], indicator_warmup_reason=warmup[i] if count[i] < 35 else None,
                          missing_market_sessions_before=int(missing[i]))
            values['intraday_high_change_pct_reason'] = high_reasons[i]
            result[self.symbols[i]] = values
        return result
