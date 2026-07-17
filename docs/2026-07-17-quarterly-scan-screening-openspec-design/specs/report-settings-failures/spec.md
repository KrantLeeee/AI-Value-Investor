# Spec: Report Jobs, Settings Toggles, Failure Visualization

## ADDED Requirements

### Requirement: Row-Level Report Job State

The system MUST show report generation state per ticker row in the recent valuation or campaign result table.

#### Scenario: Report not started

Given a result row has no report job
And no existing report file can be matched
When the table renders
Then the report button MUST show `生成研报`
And clicking it MUST create a report job for that ticker

#### Scenario: Report running

Given a report job has status `running`
When the table renders
Then the report button MUST show `生成中`
And clicking it MUST open the report job detail page
And no duplicate running report job MUST be created for the same ticker and campaign

#### Scenario: Report completed

Given a report job has status `completed`
And report_path exists under `output/` or `output/reports/`
When the user clicks the row button
Then the system MUST open the report reader page

#### Scenario: Report file missing

Given a report job has status `completed`
But report_path no longer exists
When report button state is computed
Then status MUST become `failed`
And failure_type MUST be `report_file_missing`

### Requirement: Report Source Linkage

The system MUST preserve scan source metadata for generated reports.

#### Scenario: Report generated from campaign result

Given the user starts report generation from a campaign result row
When the report job is created
Then it MUST store source_campaign_id, source_scan_id when available, source_strategy_id, and ticker

#### Scenario: Read report with source

Given a report has source_campaign_id
When the user opens the report reader page
Then the page MUST display a report source panel
And the panel MUST link back to the campaign, strategy, and ticker detail pages

### Requirement: Boolean Settings Toggle

The settings page MUST render whitelisted boolean settings as toggles.

#### Scenario: Boolean setting renders as toggle

Given config key `SKIP_AKSHARE` is whitelisted as boolean
When the user opens settings
Then the setting MUST render as a toggle
And it MUST NOT render as a free text input

#### Scenario: Save toggle

Given the user toggles `L0_HYGIENE_FILTER`
When the backend saves successfully
Then `.env` MUST be updated through the existing backup flow
And `os.environ` MUST be updated
And `app_settings` MUST store the new value
And the page MUST display the new toggle state within 2 seconds

#### Scenario: Reject unknown setting

Given the user submits config_key `UNKNOWN_FLAG`
When the backend validates the toggle request
Then the request MUST be rejected
And `.env` MUST NOT be modified

#### Scenario: Save failure rollback

Given writing `.env` fails
When the user toggles a setting
Then the UI MUST display the original value
And the error MUST be shown to the user

### Requirement: Failure Visualization

The system MUST classify and display data source, LLM, local DB, report file, and screening data failures.

#### Scenario: Data source timeout

Given a ticker scan raises a timeout
When the failure is recorded
Then failure_type MUST be `data_source_timeout`
And retryable MUST be true
And user_action MUST be `重试失败项`

#### Scenario: Tushare authentication failure

Given a Tushare request fails due to token or authentication
When the failure is recorded
Then failure_type MUST be `tushare_auth_failed`
And user_action MUST be `打开配置`

#### Scenario: LLM quota failure

Given a report job fails due to LLM quota
When the failure is recorded
Then failure_type MUST be `llm_quota_failed`
And the report job status MUST be `failed`
And the job detail page MUST display the failure type

#### Scenario: Unknown failure

Given an error cannot be classified
When the failure is recorded
Then failure_type MUST be `unknown_error`
And raw_error MUST be stored as a summarized detail

### Requirement: History Comparison

The system MUST show quarterly campaign history comparisons.

#### Scenario: View latest four quarters

Given at least four campaigns exist
When the user opens campaign history
Then the page MUST show the latest four quarters by default
And each row MUST include total_count, completed_count, filter_rejected_count, strong_candidate_count, deep_research_count, and failed_count

#### Scenario: No history

Given no previous campaigns exist
When the user opens campaign history
Then the page MUST show an empty state
