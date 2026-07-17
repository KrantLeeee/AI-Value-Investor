# Spec: Screening Filter Engine

## ADDED Requirements

### Requirement: L0 Hygiene Filter

The system MUST execute L0 hygiene rules before valuation.

#### Scenario: ST stock rejected

Given a ticker name contains `ST`, `*ST`, or `退`
When the L0 filter runs
Then filter_status MUST be `rejected_l0`
And failed_rules MUST include `l0_non_st`
And reject_reason MUST include the ST or delisting risk reason
And valuation MUST NOT run for the ticker

#### Scenario: Listing age below five years

Given a ticker has been listed for less than 5 years
When the L0 filter runs
Then filter_status MUST be `rejected_l0`
And failed_rules MUST include `l0_listing_age`
And reject_reason MUST include `上市不满5年`

#### Scenario: Listing date missing

Given the ticker listing date is missing
When the L0 filter runs
Then the related rule result MUST be `manual_review`
And missing_rules MUST include `l0_listing_age`
And the ticker MUST NOT enter strong_candidate

#### Scenario: Financial real estate exclusion

Given the ordinary value strategy excludes financial and real estate sectors
And the ticker sector is bank, insurance, broker, real estate, or quasi-finance
When the L0 filter runs
Then filter_status MUST be `rejected_l0`
And failed_rules MUST include `l0_industry_exclusion`

### Requirement: L1 Quality Filter

The system MUST execute L1 quality rules for tickers that pass L0.

#### Scenario: ROE rule rejected

Given a ticker has a 5-year ROE value below the configured threshold
When the L1 filter runs
Then filter_status MUST be `rejected_l1`
And failed_rules MUST include `l1_roe_5y`
And reject_reason MUST include `ROE`

#### Scenario: Owner earnings rejected

Given owner earnings are negative or cumulative owner earnings / net income is below 70%
When the L1 filter runs
Then filter_status MUST be `rejected_l1`
And failed_rules MUST include `l1_owner_earnings`

#### Scenario: Cash collection rejected

Given sales cash collection / revenue is below 85% in the configured period
When the L1 filter runs
Then filter_status MUST be `rejected_l1`
And failed_rules MUST include `l1_cash_collection`

#### Scenario: Multiple missing L1 inputs

Given multiple key L1 input fields are missing
When the L1 filter runs
Then filter_status MUST be `rejected_missing_data` or `manual_review`
And missing_rules MUST list every missing key rule
And valuation MUST NOT run for strong candidate classification

### Requirement: Red Flag Filter

The system MUST execute red flag rules for tickers that pass L0 and L1.

#### Scenario: High goodwill rejected

Given goodwill / equity is greater than 30%
When the red flag filter runs
Then filter_status MUST be `rejected_red_flag`
And failed_rules MUST include `rf_goodwill_high`
And valuation MUST NOT run for candidate classification

#### Scenario: Cash debt double high rejected

Given cash / equity is greater than 40%
And interest-bearing debt / equity is greater than 30%
When the red flag filter runs
Then filter_status MUST be `rejected_red_flag`
And failed_rules MUST include `rf_cash_debt_double_high`

#### Scenario: Pledge data missing

Given controlling shareholder pledge data is unavailable
When the red flag filter runs
Then rule result MUST be `missing`
And missing_rules MUST include `rf_pledge_high`
And the rule MUST NOT be counted as pass

### Requirement: Valuation Gate

The system MUST run valuation only for tickers that pass all blocking filter rules.

#### Scenario: Rejected ticker skips valuation

Given a ticker has filter_status `rejected_l1`
When the runner processes the ticker
Then `run_batch_valuation()` MUST NOT be called for that ticker
And a filter-only master result row MUST be saved

#### Scenario: Passed ticker runs valuation

Given a ticker passes L0, L1, and red_flag filters
When the runner processes the ticker
Then `run_batch_valuation()` MUST be called
And the valuation result MUST be merged with filter_status `passed`

### Requirement: Rule Evaluation Persistence

The system MUST persist every enabled rule's evaluation for every processed ticker.

#### Scenario: Persist rule trace

Given a ticker is processed by the filter engine
When filter evaluation completes
Then `scan_rule_evaluations` MUST contain one row per evaluated rule
And each row MUST include rule_id, rule_layer, input_value, threshold_value, result, severity, reason, data_source, and period_range
