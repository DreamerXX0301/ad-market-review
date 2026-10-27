"""Batch-local numerical validation for already normalized strategy quotes.

Only the normalizer creates this carrier: prices are finite floats or None.
Coverage, duplicate dates, calendar membership and period completeness still
belong to each strict indicator. Ordinary lists use their original validator.
"""


class NormalizedMetricRows(list):
    def positive_ohlcv(self):
        """Return row identities with valid traded OHLC, without rounding."""
        if len(self) < 64:
            return None
        import numpy as np
        values = np.asarray([(row['open'], row['high'], row['low'], row['close'], row['volume'])
                             for row in self], dtype=np.float64)
        valid = np.isfinite(values).all(axis=1) & (values > 0).all(axis=1)
        opening, high, low, close, _ = values.T
        valid &= (low <= np.minimum(opening, close)) & (np.maximum(opening, close) <= high)
        return {id(row) for row, ready in zip(self, valid) if ready}
