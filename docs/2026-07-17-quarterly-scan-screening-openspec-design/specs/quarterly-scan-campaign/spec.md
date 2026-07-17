# Spec: Quarterly Scan Campaign

## ADDED Requirements

### Requirement: Campaign Creation

The system MUST allow a local user to create a quarterly `scan_campaign` from a selected market scope, screening strategy, batch size, and scan options.

#### Scenario: Create A-share campaign

Given the local `stock_universe` contains A-share rows
And the user selects market_scope `a_share`
And the user sets batch_size to `100`
When the user submits the campaign creation form
Then the system creates a `scan_campaign`
And the system creates an immutable `universe_snapshot`
And the system creates a `strategy_snapshot`
And the system splits the snapshot into `scan_batch` rows
And no batch contains more than `100` tickers

#### Scenario: Reject oversized batch

Given the user sets batch_size to `800`
When the user submits the campaign creation form
Then the system MUST reject the request
And the response MUST display `单批最多 500 只`
And no `scan_campaign` is created

#### Scenario: Empty universe

Given the selected market scope has no available tickers
When the user submits the campaign creation form
Then the system MUST reject the request
And the response MUST display an actionable empty-state message
And no `scan_campaign` is created

### Requirement: Universe Snapshot

The system MUST freeze the ticker universe at campaign creation time so resumed scans do not drift when `stock_universe` is later refreshed.

#### Scenario: Stock universe changes after campaign creation

Given a campaign was created with ticker `002236.SZ` at item_index `10`
And the local `stock_universe` is refreshed afterwards
When the user resumes the campaign
Then the runner MUST read ticker order from `scan_campaign_items`
And ticker `002236.SZ` MUST remain at item_index `10`

### Requirement: Campaign Resume

The system MUST recover unfinished campaigns after service restart.

#### Scenario: Resume unfinished campaign

Given a campaign has status `running`
And the campaign has completed 1300 of 5529 tickers
And the local console process restarts
When the user opens the local console
Then the homepage MUST show the unfinished campaign
And the homepage MUST show completed_count `1300`
And the homepage MUST show remaining_count `4229`
And the user MUST be able to continue scanning

#### Scenario: Continue from next ticker

Given a campaign has status `paused`
And ticker `002236.SZ` is the `next_ticker`
When the user clicks continue
Then the runner MUST start from `002236.SZ`
And previously completed tickers MUST NOT be scanned again

#### Scenario: Retry failed only

Given a campaign contains failed tickers
And the user selects `retry_failed`
When the runner starts
Then only items with `scan_status = failed` MUST be selected
And successful, skipped, and rejected items MUST NOT be rescanned

### Requirement: Campaign Control

The system MUST support safe pause, stop, continue, and retry failed controls.

#### Scenario: Pause running campaign

Given campaign_status is `running`
When the user clicks pause
Then the system MUST set control_flag to `pause_requested`
And the runner MUST finish the current ticker
And campaign_status MUST become `paused`
And the page MUST display the paused state within the next status refresh

#### Scenario: Stop running campaign

Given campaign_status is `running`
When the user clicks stop
Then the system MUST set control_flag to `stop_requested`
And the runner MUST finish the current ticker or timeout safely
And campaign_status MUST become `stopped`

#### Scenario: Consecutive failures auto-pause

Given a campaign is running
And ten consecutive tickers fail due to data source timeout
When the runner records the tenth failure
Then campaign_status MUST become `paused`
And the latest failure MUST include failure_type `data_source_timeout`
And the page MUST offer a retry failed action

### Requirement: Master Result

The system MUST maintain a campaign-level `master_result` that updates after each ticker.

#### Scenario: Successful ticker valuation

Given a ticker passes screening
When valuation completes
Then `scan_master_results` MUST contain one latest row for the ticker
And the row MUST include filter_status, current_price, intrinsic_value, margin_of_safety_pct, action, confidence, quality_score, and data_completeness

#### Scenario: Re-scan overwrites latest row

Given ticker `002236.SZ` already has a master result row in a campaign
When the ticker is rescanned within the same campaign
Then `scan_master_results` MUST update the existing row
And historical batch/item records MUST remain available

### Requirement: Campaign Exports

The system MUST export campaign results as CSV or JSON without deleting local output files.

#### Scenario: Export current result

Given a campaign has completed at least one ticker
When the user clicks export current result
Then the system MUST write a master result export
And the export MUST include only structured snake_case fields

#### Scenario: Export empty result

Given a campaign has no completed, skipped, rejected, or failed rows
When the user views the campaign page
Then export buttons MUST be disabled or hidden
