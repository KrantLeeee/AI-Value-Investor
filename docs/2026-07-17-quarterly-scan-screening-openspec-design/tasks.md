# Tasks: Quarterly Scan Screening Workbench

## 1. Data Layer

- [ ] Add campaign schema to `src/data/database.py`.
- [ ] Add idempotent indexes for campaign, items, rule evaluations, master results, failures, report jobs.
- [ ] Add CRUD helpers for `scan_campaigns`.
- [ ] Add CRUD helpers for `scan_batches`.
- [ ] Add CRUD helpers for `scan_campaign_items`.
- [ ] Add CRUD helpers for `screening_strategy_snapshots`.
- [ ] Add CRUD helpers for `scan_rule_evaluations`.
- [ ] Add CRUD helpers for `scan_master_results`.
- [ ] Add CRUD helpers for `scan_failures`.
- [ ] Add CRUD helpers for `report_jobs`.
- [ ] Add CRUD helpers for `app_settings`.
- [ ] Add tests for schema creation and basic CRUD.

## 2. Campaign Service

- [ ] Create `src/screening/models.py`.
- [ ] Define campaign, batch, filter, rule, report, failure enums.
- [ ] Create `src/screening/campaigns.py`.
- [ ] Implement `create_campaign()`.
- [ ] Enforce `batch_size <= 500`.
- [ ] Reject empty universe with actionable error.
- [ ] Create immutable `universe_snapshot` rows.
- [ ] Split campaign items into batches.
- [ ] Implement campaign summary aggregation.
- [ ] Implement latest unfinished campaign lookup.
- [ ] Implement export path generation.
- [ ] Add unit tests for campaign creation and validation.

## 3. Strategy Snapshot

- [ ] Create `src/screening/strategy.py`.
- [ ] Load `config/screening_rules.yaml` read-only.
- [ ] Provide builtin default strategy if config does not contain new layered rules.
- [ ] Validate rule ids, layers, enabled flags, thresholds, missing policies, data sources.
- [ ] Return line-level errors when YAML parse fails.
- [ ] Create immutable strategy snapshot with config hash.
- [ ] Implement rule count aggregation by campaign.
- [ ] Add tests for valid config, invalid config, builtin fallback, immutable snapshot.

## 4. Filter Engine

- [ ] Create `src/screening/filter_engine.py`.
- [ ] Implement `RuleEvaluation`.
- [ ] Implement `FilterResult`.
- [ ] Implement L0 `l0_non_st`.
- [ ] Implement L0 `l0_listing_age`.
- [ ] Implement L0 `l0_audit_opinion` with missing handling.
- [ ] Implement L0 `l0_industry_exclusion`.
- [ ] Implement L1 `l1_roe_5y`.
- [ ] Implement L1 `l1_owner_earnings`.
- [ ] Implement L1 `l1_cash_collection`.
- [ ] Implement L1 `l1_leverage`.
- [ ] Implement L1 `l1_gross_margin`.
- [ ] Implement L1 `l1_share_dilution`.
- [ ] Implement red flag `rf_cash_debt_double_high`.
- [ ] Implement red flag `rf_receivable_surge`.
- [ ] Implement red flag `rf_other_receivable`.
- [ ] Implement red flag `rf_goodwill_high`.
- [ ] Implement red flag `rf_pledge_high`.
- [ ] Implement red flag `rf_investigation`.
- [ ] Persist every rule evaluation.
- [ ] Add unit tests for pass, reject, missing, manual_review paths.

## 5. Campaign Runner

- [ ] Create `src/screening/campaign_runner.py`.
- [ ] Implement single active runner guard.
- [ ] Implement `continue_last`.
- [ ] Implement `scan_remaining`.
- [ ] Implement `retry_failed`.
- [ ] Treat `restart_new` as campaign creation, not destructive reset.
- [ ] Mark current ticker and current stage before each phase.
- [ ] Persist progress after every ticker.
- [ ] Skip valuation when filter result blocks valuation.
- [ ] Merge filter result with `run_batch_valuation()` output.
- [ ] Upsert `scan_master_results` per ticker.
- [ ] Update campaign aggregate counts.
- [ ] Write incremental `master_result.json/csv`.
- [ ] Check `pause_requested` after each ticker.
- [ ] Check `stop_requested` after each ticker or timeout.
- [ ] Auto-pause after consecutive failure threshold.
- [ ] Add tests for pause, resume, retry failed, restart recovery.

## 6. Valuation Integration

- [ ] Extend `ValuationSnapshot` with filter fields.
- [ ] Add helper to serialize filter fields into scan JSON/CSV.
- [ ] Keep existing `save_valuation_scan()` fields backward compatible.
- [ ] Add campaign metadata to valuation scan output.
- [ ] Add tests that legacy consumers can still read latest scan.

## 7. Failure Handling

- [ ] Create `src/screening/failures.py`.
- [ ] Map timeout errors to `data_source_timeout`.
- [ ] Map Tushare auth/token errors to `tushare_auth_failed`.
- [ ] Map LLM auth errors to `llm_auth_failed`.
- [ ] Map LLM quota errors to `llm_quota_failed`.
- [ ] Map SQLite lock to `local_db_locked`.
- [ ] Map missing report file to `report_file_missing`.
- [ ] Map key screening gaps to `screening_data_missing`.
- [ ] Persist failure records.
- [ ] Add tests for classifier mappings.

## 8. Report Jobs

- [ ] Create `src/screening/report_jobs.py`.
- [ ] Persist per-ticker report job rows in SQLite.
- [ ] Mirror latest job to `output/report_jobs/latest.json`.
- [ ] Import existing latest report job on startup when applicable.
- [ ] Add duplicate-running-job guard by ticker + campaign.
- [ ] Store source scan, campaign, strategy ids.
- [ ] Store events timeline.
- [ ] Add report button state lookup for a result table.
- [ ] Add tests for create, running, completed, failed, duplicate click, missing file.

## 9. Web: Campaign Pages

- [ ] Add navigation link for quarterly campaigns.
- [ ] Add `/campaigns` list page.
- [ ] Add `/campaigns/new` create form.
- [ ] Add `/campaigns/{campaign_id}` workbench.
- [ ] Add progress bar and current ticker area.
- [ ] Add batch summary cards.
- [ ] Add status-based control buttons.
- [ ] Add failure panel.
- [ ] Add master result table with filter columns.
- [ ] Add export buttons.
- [ ] Add JSON status endpoint.
- [ ] Add 5-second vanilla JS polling or meta refresh fallback.
- [ ] Add local console tests for routes and form validation.

## 10. Web: Strategy Inspector

- [ ] Add `/campaigns/{campaign_id}/strategy`.
- [ ] Render strategy snapshot header.
- [ ] Render funnel counts.
- [ ] Render rule table with pass/reject/missing counts.
- [ ] Add `/campaigns/{campaign_id}/rules/{rule_id}`.
- [ ] Render rejected ticker list for selected rule.
- [ ] Render missing data list for selected rule.
- [ ] Add `/campaigns/{campaign_id}/tickers/{ticker}`.
- [ ] Render ticker rule evaluation detail.
- [ ] Link ticker detail to valuation detail and report job.

## 11. Web: Report Rows

- [ ] Update recent valuation scan table to include report button state.
- [ ] Add `POST /actions/report-jobs/create`.
- [ ] Add `/report-jobs/{report_job_id}`.
- [ ] Update report reader to show source panel when source ids exist.
- [ ] Add JSON endpoint for row report button states.

## 12. Web: Settings Toggles

- [ ] Define boolean config whitelist.
- [ ] Render whitelist booleans as toggles instead of text inputs.
- [ ] Keep secrets as password inputs.
- [ ] Implement `POST /actions/settings/toggle`.
- [ ] Use existing `.env` backup flow.
- [ ] Persist saved value in `app_settings`.
- [ ] Reject unknown keys.
- [ ] Add tests for toggle save, failure rollback, unknown key.

## 13. Exports

- [ ] Implement master result export.
- [ ] Implement candidates export.
- [ ] Implement rejected details export.
- [ ] Implement failures export.
- [ ] Use temp file then atomic rename.
- [ ] Disable export buttons for empty results.
- [ ] Add tests for CSV headers and empty export behavior.

## 14. History

- [ ] Add campaign history query.
- [ ] Add `/campaigns/{campaign_id}/history`.
- [ ] Show latest 4 quarters by default.
- [ ] Include total, completed, filter rejected, strong candidate, deep research, failed counts.
- [ ] Display scope and strategy for comparison of screening criteria.

## 15. Verification

- [ ] Run `poetry run pytest tests/test_campaigns.py -v`.
- [ ] Run `poetry run pytest tests/test_filter_engine.py -v`.
- [ ] Run `poetry run pytest tests/test_campaign_runner.py -v`.
- [ ] Run `poetry run pytest tests/test_report_jobs.py -v`.
- [ ] Run `poetry run pytest tests/test_local_console_campaigns.py -v`.
- [ ] Run `poetry run pytest tests/ -v`.
- [ ] Manually test local console create/pause/resume/export/report flow.
