# Spec: Screening Strategy Inspector

## ADDED Requirements

### Requirement: Strategy Snapshot

The system MUST create an immutable `screening_strategy` snapshot when a campaign is created.

#### Scenario: Campaign uses strategy snapshot

Given the user creates a campaign with strategy_id `buffett_quality_v1`
When the campaign is stored
Then the system MUST store a `strategy_snapshot_id`
And the snapshot MUST include strategy_id, strategy_name, strategy_version, config_hash, and config_json

#### Scenario: Strategy changes after campaign starts

Given a campaign has already started
And the strategy source file changes afterwards
When the user opens the strategy page for that campaign
Then the page MUST display the campaign's strategy snapshot
And the page MUST NOT display changed rules as if they were used by the campaign

### Requirement: Strategy Validation

The system MUST validate the screening strategy before creating a campaign.

#### Scenario: Invalid strategy config

Given the strategy config cannot be parsed or contains invalid rule definitions
When the user creates a campaign
Then the system MUST block campaign creation
And the UI MUST show the validation error
And the response MUST complete within 2 seconds for local config validation

#### Scenario: Strategy source missing layered rules

Given the strategy config file exists but does not define L0/L1/red_flag layered rules
When the user creates a campaign
Then the system MAY use the builtin default layered strategy
And the strategy snapshot MUST mark the source as builtin default

### Requirement: Strategy Inspector Page

The system MUST provide a strategy detail page that displays rule layers, thresholds, data sources, and campaign-level counts.

#### Scenario: View strategy funnel

Given a campaign exists
When the user opens `/campaigns/{campaign_id}/strategy`
Then the page MUST show the strategy snapshot header
And the page MUST show L0, L1, red_flag, and valuation funnel layers
And each layer MUST show pass_count, reject_count, missing_count, and manual_review_count

#### Scenario: View rule cards

Given a campaign has rule evaluations
When the user opens the strategy detail page
Then the page MUST show each rule's rule_id, rule_name, rule_layer, enabled flag, threshold, data_source, pass_count, reject_count, and missing_count

### Requirement: Rule Drilldown

The system MUST allow the user to inspect which tickers were rejected or marked missing by a selected rule.

#### Scenario: Rule rejected ticker list

Given rule `l1_roe_5y` rejected one or more tickers
When the user opens `/campaigns/{campaign_id}/rules/l1_roe_5y`
Then the page MUST show the rejected ticker list
And each row MUST include ticker, name, result, and reject_reason

#### Scenario: Rule missing data list

Given rule `l0_audit_opinion` has missing data evaluations
When the user opens the rule detail page
Then the page MUST show tickers with result `missing` or `manual_review`
And each row MUST include the missing data source

### Requirement: Ticker Rule Trace

The system MUST show the complete rule evaluation path for each ticker.

#### Scenario: View ticker filter detail

Given ticker `002236.SZ` exists in a campaign
When the user opens `/campaigns/{campaign_id}/tickers/002236.SZ`
Then the page MUST show all L0, L1, and red_flag rule evaluations for the ticker
And each rule row MUST include input_value, threshold_value, result, reason, data_source, and period_range
And the page MUST show the final filter_status and reject_reason

#### Scenario: Missing data does not become pass

Given a key rule lacks required input data
When the ticker detail page renders the rule
Then the rule result MUST be `missing` or `manual_review`
And the page MUST NOT display the rule as `pass`
