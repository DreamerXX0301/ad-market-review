ALTER TABLE market_news_snapshot ADD COLUMN window_group TEXT
    CHECK(window_group IN ('weekend','holiday'));
ALTER TABLE market_news_snapshot ADD COLUMN group_cutoff_at TEXT
    CHECK((window_group IS NULL AND group_cutoff_at IS NULL)
        OR (window_group IS NOT NULL AND group_cutoff_at IS NOT NULL AND window_start_at IS NOT NULL
            AND window_start_at<group_cutoff_at AND group_cutoff_at<=cutoff_at));
