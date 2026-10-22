"""Bounded append-only context retention; caller owns the short transaction."""

SUMMARY_COLUMNS = 'snapshot_id,trade_date,recorded_at_ms,record_mode,model,rule_version,input_fingerprint,content_sha256'


def by_key(connection, day, version, fingerprint):
    return connection.execute('''SELECT * FROM market_sentiment_snapshot
        WHERE trade_date=? AND rule_version=? AND input_fingerprint=?''',
        (day, version, fingerprint)).fetchone()


def by_id(connection, snapshot_id):
    return connection.execute('SELECT * FROM market_sentiment_snapshot WHERE snapshot_id=?', (snapshot_id,)).fetchone()


def list_records(connection, day, known_at_ms, limit, offset):
    clauses = []; args = []
    if day is not None: clauses.append('trade_date=?'); args.append(day)
    if known_at_ms is not None: clauses.append('recorded_at_ms<=?'); args.append(known_at_ms)
    where = ' WHERE '+' AND '.join(clauses) if clauses else ''
    total = connection.execute('SELECT count(*) FROM market_sentiment_snapshot'+where, args).fetchone()[0]
    items = connection.execute('SELECT '+SUMMARY_COLUMNS+' FROM market_sentiment_snapshot'+where+
                               ' ORDER BY recorded_at_ms DESC,snapshot_id DESC LIMIT ? OFFSET ?',
                               (*args, limit, offset)).fetchall()
    return items, total


def append(connection, item):
    columns = tuple(item)
    connection.execute('INSERT INTO market_sentiment_snapshot ('+','.join(columns)+') VALUES ('+
                       ','.join('?' for _ in columns)+')', tuple(item[key] for key in columns))
