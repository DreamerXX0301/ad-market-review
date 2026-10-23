"""Bounded SQLite reads for daily manual attribution; caller owns the transaction."""


def latest(connection, day, symbol):
    return connection.execute(
        'SELECT * FROM market_mainline_revision WHERE trade_date=? AND instrument_id=? ORDER BY revision DESC LIMIT 1',
        (day, symbol)).fetchone()


def by_date(connection, day, known_at_ms=None):
    cutoff = '' if known_at_ms is None else ' AND recorded_at_ms<=?'
    args = (day,) if known_at_ms is None else (day, known_at_ms)
    # Select revision keys first, so large historical evidence JSON is not copied
    # into a window-function working set merely to discard superseded versions.
    return connection.execute('''SELECT record.* FROM market_mainline_revision record JOIN (
        SELECT instrument_id,max(revision) AS revision FROM market_mainline_revision
        WHERE trade_date=?''' + cutoff + ''' GROUP BY instrument_id
        ) head ON record.trade_date=? AND record.instrument_id=head.instrument_id AND record.revision=head.revision
        ORDER BY record.instrument_id''', (*args, day)).fetchall()


def history(connection, day, symbol, limit, offset):
    args = (day, symbol)
    total = connection.execute('SELECT count(*) FROM market_mainline_revision WHERE trade_date=? AND instrument_id=?', args).fetchone()[0]
    items = connection.execute('''SELECT * FROM market_mainline_revision WHERE trade_date=? AND instrument_id=?
        ORDER BY revision DESC LIMIT ? OFFSET ?''', (*args, limit, offset)).fetchall()
    return items, total


def append(connection, item):
    columns = tuple(item)
    connection.execute('INSERT INTO market_mainline_revision (' + ','.join(columns) + ') VALUES (' + ','.join('?' for _ in columns) + ')',
                       tuple(item[key] for key in columns))
