//! Round-trips every generated fixture (packages/shared-types/fixtures) through the Rust types.
//! Python produced them from the Pydantic source of truth; equality after Rust
//! deserialize -> serialize proves both sides agree on every field, enum value and null.

mod common;

use std::fs;

use lead_intelligence_lib::data::{
    AiBenchmarkRequest, AiConfigureRequest, AiInferenceDetail, AiInferenceListQuery,
    AiInferencePage, AiInferenceQuery, AiModelChangeRequest, AiReviewRequest, AiReviewStatus,
    AiRunListQuery, AiRunPage, AiStartRequest, AiStatus, AiStatusQuery, AssessmentMethod,
    BulkRequest, BulkResult, BusinessDetail, BusinessListQuery, BusinessPage, CalibrationQuery,
    CalibrationReport, CampaignListQuery, CampaignPage, CampaignSaved, CandidateDetail,
    CandidateListQuery, CandidatePage, CanonicalChoiceRequest, ChecklistCatalog,
    ChecklistCatalogQuery, ChecklistChangeRequest, ChecklistSaveRequest, ContactActionRequest,
    ContactDetail, ContactListQuery, ContactOrigin, ContactPage, ContactRunListQuery,
    ContactRunPage, ContactStartRequest, CrawlRunListQuery, CrawlRunPage, CrawlStartRequest,
    CrawlUrlDetail, CrawlUrlListQuery, CrawlUrlPage, CrawlUrlStatus, CriteriaCheck,
    DatasetCleanupRequest, DatasetCleanupResult, DatasetDiff, DatasetDiffQuery, DatasetListQuery,
    DatasetPage, DatasetSnapshotDetail, DatasetSnapshotListQuery, DatasetSnapshotPage,
    DatasetSnapshotQuery, DatasetSnapshotRequest, DeadLetterRequest, DecisionMakerListQuery,
    DecisionMakerPage, DecisionMakerReview, DecisionMakerReviewRequest, DiscoveryControlRequest,
    DiscoveryRunListQuery, DiscoveryRunPage, DiscoveryStartRequest, EventCleanupRequest,
    EventCleanupResult, EvidenceDetail, EvidenceQuery, EvidenceStatus, ExplorerCatalog,
    ExplorerCatalogQuery, ExplorerPage, ExplorerQuery, ExportCancelRequest, ExportCatalog,
    ExportCatalogQuery, ExportDraft, ExportItem, ExportListQuery, ExportPage,
    ExportPresetSaveRequest, ExportPreview, ExportPreviewRequest, ExportPreviewResult,
    ExportStartRequest, IcpProfileView, IcpSaveRequest, Id, ImportPreview, JobEventPage,
    JobEventQuery, LatinoDetail, LatinoReviewFilter, LatinoReviewRequest, LatinoRunListQuery,
    LatinoRunPage, LatinoStartRequest, LatinoStatus, LeadActionRequest, LeadCompareQuery,
    LeadComparison, LeadScorePage, LeadScoreQuery, LeadScorecardDetail, LeadTier,
    MatchActionRequest, MatchDetail, MatchListQuery, MatchPage, MatchStatus, PipelineListQuery,
    PipelinePage, PipelineRun, PipelineStartRequest, ResearchActionRequest, ResearchWorkspace,
    ResearchWorkspaceQuery, ResolutionRunListQuery, ResolutionRunPage, ResolutionStartRequest,
    SalesBrief, SalesBriefQuery, SavedViewChangeRequest, SavedViewItem, SavedViewList,
    SavedViewQuery, SavedViewSaveRequest, ScoreCompareQuery, ScoreComparison, ScoreRunListQuery,
    ScoreRunPage, ScorecardQuery, ScoringCatalog, ScoringCatalogQuery, ScoringChangeRequest,
    ScoringModelSaveRequest, ScoringModelView, ScoringSimulation, ScoringSimulationRequest,
    ScoringStartRequest, SourceCatalog, SourceHealth, SuppressionChangeRequest, SuppressionExport,
    SuppressionExportRequest, SuppressionImportRequest, SuppressionImportResult,
    SuppressionListQuery, SuppressionPage, SyntheticSeedResult, TimelinePage, TimelineQuery,
    ValidationStatus, WebsiteAuditDetail, WebsiteAuditListQuery, WebsiteAuditPage,
    WebsiteAuditQuery, WebsiteAuditStatus, WebsiteFindingResult, WebsiteRunListQuery,
    WebsiteRunPage, WebsiteStartRequest,
};
use lead_intelligence_lib::protocol::{
    DATA_CONTRACT_VERSION, DiagnosticsReport, DiagnosticsResult, Envelope, HealthResult,
    UiErrorReport,
};
use serde::Serialize;
use serde::de::DeserializeOwned;
use serde_json::{Value, json};

fn fixture_text(name: &str) -> String {
    let path = common::repo_root()
        .join("packages/shared-types/fixtures")
        .join(name);
    fs::read_to_string(&path).unwrap_or_else(|e| panic!("{}: {e}", path.display()))
}

fn round_trip<T: DeserializeOwned + Serialize>(name: &str) -> T {
    let original: Value = serde_json::from_str(&fixture_text(name)).expect("valid JSON");
    let typed: T =
        serde_json::from_value(original.clone()).unwrap_or_else(|e| panic!("{name}: {e}"));
    let again = serde_json::to_value(&typed).expect("serializable");
    assert_eq!(again, original, "{name} changed after a Rust round-trip");
    typed
}

#[test]
fn every_fixture_round_trips_through_rust_types() {
    round_trip::<Envelope>("envelope.request.health.json");

    let ready: Envelope = round_trip("envelope.event.ready.json");
    let health: HealthResult = ready.into_payload().expect("ready payload");
    assert_eq!(health.data_contract_version, DATA_CONTRACT_VERSION);

    let diagnostics: Envelope = round_trip("envelope.result.diagnostics.json");
    let payload = Value::Object(diagnostics.payload.clone().expect("payload"));
    let result: DiagnosticsResult = diagnostics.into_payload().expect("diagnostics payload");
    assert_eq!(
        serde_json::to_value(&result).expect("serializable"),
        payload
    );

    let error: Envelope = round_trip("envelope.result.error.json");
    assert!(error.payload.is_none() && error.error.is_some());

    round_trip::<DiagnosticsReport>("diagnostics_report.json");
    round_trip::<DiagnosticsReport>("diagnostics_report.worker_down.json");
    round_trip::<UiErrorReport>("ui_error_report.json");
}

#[test]
fn data_contract_fixtures_round_trip_and_pass_validation() {
    let query: BusinessListQuery = round_trip("business_list_query.json");
    query.validate().expect("fixture query is valid");
    let page: BusinessPage = round_trip("business_page.json");
    page.validate(&query)
        .expect("fixture page matches its query");

    let detail: BusinessDetail = round_trip("business_detail.json");
    detail
        .validate(&page.items[0].id)
        .expect("fixture detail describes the first business");
    assert!(detail.is_synthetic);
    assert!(
        detail.validate(&page.items[1].id).is_err(),
        "a detail for another business is refused"
    );

    let datasets_query: DatasetListQuery = round_trip("dataset_list_query.json");
    let datasets: DatasetPage = round_trip("dataset_page.json");
    datasets
        .validate(&datasets_query)
        .expect("fixture dataset page matches its query");
    round_trip::<SyntheticSeedResult>("synthetic_seed_result.json");
}

type Mutation = (&'static str, fn(&mut Value));

#[test]
fn tampered_data_payloads_are_rejected() {
    let original: Value =
        serde_json::from_str(&fixture_text("business_detail.json")).expect("JSON");
    let mutations: [Mutation; 5] = [
        ("unknown field", |v| v["business"]["surprise"] = true.into()),
        ("unknown enum value", |v| {
            v["business"]["status"] = "bogus".into()
        }),
        ("second-precision timestamp", |v| {
            v["business"]["created_at"] = "2026-01-01T00:00:00Z".into();
        }),
        ("confidence above one", |v| {
            v["names"][0]["value"]["confidence"] = 1.5.into();
        }),
        ("upper-case id", |v| {
            v["business"]["id"] = "01900000-0000-7000-8000-00000000000A".into();
        }),
    ];
    for (label, mutate) in mutations {
        let mut tampered = original.clone();
        mutate(&mut tampered);
        assert!(
            serde_json::from_value::<BusinessDetail>(tampered).is_err(),
            "accepted a payload with {label}"
        );
    }
}

#[test]
fn discovery_fixtures_round_trip_and_pass_validation() {
    let catalog: SourceCatalog = round_trip("source_catalog.json");
    catalog.validate().expect("fixture catalogue is usable");
    assert!(
        catalog
            .sources
            .iter()
            .any(|source| source.metadata.requires_credentials
                && source.health == SourceHealth::NeedsConfiguration),
        "a source needing credentials must say so instead of looking ready"
    );

    let campaign_query: CampaignListQuery = round_trip("campaign_list_query.json");
    campaign_query.validate().expect("fixture query is valid");
    let campaigns: CampaignPage = round_trip("campaign_page.json");
    campaigns
        .validate(&campaign_query)
        .expect("fixture page matches its query");
    campaigns.items[0]
        .criteria
        .validate()
        .expect("fixture criteria are valid");

    let saved: CampaignSaved = round_trip("campaign_saved.json");
    assert_eq!(saved.campaign.id, campaigns.items[0].id);

    let check: CriteriaCheck = round_trip("criteria_check.json");
    check.validate().expect("fixture check is consistent");

    let start: DiscoveryStartRequest = round_trip("discovery_start_request.json");
    start.validate().expect("fixture request is valid");

    let run_query: DiscoveryRunListQuery = round_trip("discovery_run_list_query.json");
    run_query.validate().expect("fixture query is valid");
    let runs: DiscoveryRunPage = round_trip("discovery_run_page.json");
    runs.validate(&run_query)
        .expect("fixture run page matches its query");

    let candidate_query: CandidateListQuery = round_trip("candidate_list_query.json");
    candidate_query.validate().expect("fixture query is valid");
    let candidates: CandidatePage = round_trip("candidate_page.json");
    candidates
        .validate(&candidate_query)
        .expect("fixture candidate page matches its query");

    let detail: CandidateDetail = round_trip("candidate_detail.json");
    detail
        .validate(&detail.candidate.id.clone())
        .expect("fixture detail describes its own candidate");
    assert!(
        detail.candidate.duplicate_of_id.is_some() && detail.duplicate_of.is_some(),
        "the fixture candidate repeats an earlier one, and says which"
    );

    let preview: ImportPreview = round_trip("import_preview.json");
    preview.validate().expect("fixture preview is consistent");
    assert_eq!(preview.suggested_mapping.name.as_deref(), Some("name"));
}

type DiscoveryMutation = (&'static str, fn(&mut Value));

#[test]
fn tampered_discovery_payloads_are_rejected() {
    let original: Value = serde_json::from_str(&fixture_text("candidate_page.json")).expect("JSON");
    let mutations: [DiscoveryMutation; 3] = [
        ("unknown field", |v| v["items"][0]["surprise"] = true.into()),
        ("unknown source kind", |v| {
            v["items"][0]["connector_kind"] = "telepathy".into()
        }),
        ("malformed cursor", |v| {
            v["next_cursor"] = "not a cursor".into()
        }),
    ];
    for (label, mutate) in mutations {
        let mut tampered = original.clone();
        mutate(&mut tampered);
        assert!(
            serde_json::from_value::<CandidatePage>(tampered).is_err(),
            "accepted a payload with {label}"
        );
    }

    // a page that hides a duplicate behind a filter that excluded them, and a run that inflates
    // its own totals, are both refused before they can reach the interface
    let query: CandidateListQuery = serde_json::from_value(json!({
        "project_id": null, "campaign_id": null, "source_run_id": null, "search": null,
        "include_duplicates": false, "limit": 50, "cursor": null
    }))
    .expect("valid query");
    let page: CandidatePage =
        serde_json::from_str(&fixture_text("candidate_page.json")).expect("page");
    assert!(
        page.validate(&query).is_err(),
        "the fixture page includes duplicates, so a filtered query must refuse it"
    );

    let mut runs: Value =
        serde_json::from_str(&fixture_text("discovery_run_page.json")).expect("JSON");
    runs["items"][0]["candidate_count"] = 999.into();
    let inflated: DiscoveryRunPage = serde_json::from_value(runs).expect("still a page");
    assert!(inflated.items[0].validate().is_err());
}

#[test]
fn crawl_fixtures_round_trip_and_pass_validation() {
    let start: CrawlStartRequest = round_trip("crawl_start_request.json");
    start.validate().expect("fixture request is valid");

    let run_query: CrawlRunListQuery = round_trip("crawl_run_list_query.json");
    run_query.validate().expect("fixture query is valid");
    let runs: CrawlRunPage = round_trip("crawl_run_page.json");
    runs.validate(&run_query)
        .expect("fixture crawl page matches its query");
    let run = &runs.items[0];
    assert!(run.metrics.browser_fallbacks > 0 && !run.errors.is_empty());

    let url_query: CrawlUrlListQuery = round_trip("crawl_url_list_query.json");
    url_query.validate().expect("fixture query is valid");
    let urls: CrawlUrlPage = round_trip("crawl_url_page.json");
    urls.validate(&url_query)
        .expect("fixture url page matches its query");
    assert!(
        urls.items
            .iter()
            .any(|item| item.status == CrawlUrlStatus::Skipped && item.reason.is_some()),
        "a skipped url says which policy skipped it"
    );

    let detail: CrawlUrlDetail = round_trip("crawl_url_detail.json");
    detail
        .validate(&detail.crawl_url.id.clone())
        .expect("fixture detail describes its own url");
    assert_eq!(
        detail.crawl_url.fallback_reason.as_deref(),
        Some("empty_shell")
    );
}

#[test]
fn tampered_crawl_payloads_are_rejected() {
    let mut settings: Value =
        serde_json::from_str(&fixture_text("crawl_start_request.json")).expect("JSON");
    settings["settings"]["requests_per_minute_per_host"] = 10_000.into();
    let hurried: CrawlStartRequest = serde_json::from_value(settings).expect("still a request");
    assert!(
        hurried.validate().is_err(),
        "the core refuses to hammer a host"
    );

    let empty: CrawlStartRequest = serde_json::from_value(json!({
        "project_id": null, "campaign_id": null, "urls": [],
        "settings": serde_json::from_str::<Value>(&fixture_text("crawl_start_request.json"))
            .expect("JSON")["settings"].clone()
    }))
    .expect("a request");
    assert!(empty.validate().is_err(), "nothing to crawl is refused");

    let mut runs: Value = serde_json::from_str(&fixture_text("crawl_run_page.json")).expect("JSON");
    runs["items"][0]["metrics"]["pages_fetched"] = 999.into();
    let inflated: CrawlRunPage = serde_json::from_value(runs).expect("still a page");
    assert!(inflated.items[0].validate().is_err(), "totals must add up");

    let mut urls: Value = serde_json::from_str(&fixture_text("crawl_url_page.json")).expect("JSON");
    urls["items"][0]["status"] = "downloaded".into();
    assert!(serde_json::from_value::<CrawlUrlPage>(urls).is_err());

    let detail: CrawlUrlDetail =
        serde_json::from_str(&fixture_text("crawl_url_detail.json")).expect("detail");
    let other = Id::parse("01900000-0000-7000-8000-000000009999").expect("id");
    assert!(
        detail.validate(&other).is_err(),
        "a detail for another url is refused"
    );
}

#[test]
fn resolution_fixtures_round_trip_and_pass_validation() {
    round_trip::<ResolutionStartRequest>("resolution_start_request.json");
    let run_query: ResolutionRunListQuery = round_trip("resolution_run_list_query.json");
    run_query.validate().expect("fixture query is valid");
    let runs: ResolutionRunPage = round_trip("resolution_run_page.json");
    runs.validate(&run_query)
        .expect("fixture run page matches its query");
    assert!(runs.items.iter().any(|run| run.stats.clusters > 0));

    let match_query: MatchListQuery = round_trip("match_list_query.json");
    match_query.validate().expect("fixture query is valid");
    let pairs: MatchPage = round_trip("match_page.json");
    pairs
        .validate(&match_query)
        .expect("fixture queue matches its query");
    assert!(
        pairs
            .items
            .iter()
            .any(|item| item.status == MatchStatus::Pending),
        "an ambiguous pair waits for a person"
    );

    let detail: MatchDetail = round_trip("match_detail.json");
    detail
        .validate(&detail.pair.id.clone())
        .expect("fixture detail describes its own pair");
    assert!(!detail.fields.is_empty() && !detail.reasons.is_empty());

    let action: MatchActionRequest = round_trip("match_action_request.json");
    action.validate().expect("fixture action is valid");
    assert_eq!(action.match_id, detail.pair.id);

    let cluster: BusinessDetail = round_trip("business_cluster_detail.json");
    cluster
        .validate(&cluster.business.id.clone())
        .expect("fixture cluster describes itself");
    assert!(cluster.members.len() > 1);
    assert!(
        cluster
            .canonical
            .iter()
            .any(|value| value.alternative_count > 1),
        "a conflict inside a cluster stays visible"
    );

    let choice: CanonicalChoiceRequest = round_trip("canonical_choice_request.json");
    choice.validate().expect("fixture choice is valid");
    assert_eq!(choice.business_id, cluster.business.id);
}

type ResolutionMutation = (&'static str, fn(&mut Value));

#[test]
fn tampered_resolution_payloads_are_rejected() {
    let original: Value = serde_json::from_str(&fixture_text("match_page.json")).expect("JSON");
    let mutations: [ResolutionMutation; 3] = [
        ("unknown field", |v| v["items"][0]["surprise"] = true.into()),
        ("unknown outcome", |v| {
            v["items"][0]["outcome"] = "probably".into()
        }),
        ("score above one", |v| v["items"][0]["score"] = 1.5.into()),
    ];
    for (label, mutate) in mutations {
        let mut tampered = original.clone();
        mutate(&mut tampered);
        assert!(
            serde_json::from_value::<MatchPage>(tampered).is_err(),
            "accepted a payload with {label}"
        );
    }

    // a queue that shows pairs its filter excluded is refused before it reaches the interface
    let merged_only: MatchListQuery = serde_json::from_value(json!({
        "project_id": null, "status": "merged", "risk": null, "min_score": null,
        "connector_key": null, "limit": 50, "cursor": null
    }))
    .expect("valid query");
    let page: MatchPage = serde_json::from_value(original).expect("page");
    assert!(page.validate(&merged_only).is_err());

    // an observation of another business, or a value pointing outside the cluster, is refused
    let cluster: Value =
        serde_json::from_str(&fixture_text("business_cluster_detail.json")).expect("JSON");
    let stranger = "01900000-0000-7000-8000-000000009999";
    let mut foreign = cluster.clone();
    foreign["names"][0]["value"]["business_id"] = stranger.into();
    let foreign: BusinessDetail = serde_json::from_value(foreign).expect("still a detail");
    assert!(foreign.validate(&foreign.business.id.clone()).is_err());
    let mut pointer = cluster;
    pointer["canonical"][0]["observation_id"] = stranger.into();
    let pointer: BusinessDetail = serde_json::from_value(pointer).expect("still a detail");
    assert!(pointer.validate(&pointer.business.id.clone()).is_err());

    let detail: MatchDetail =
        serde_json::from_str(&fixture_text("match_detail.json")).expect("detail");
    let other = Id::parse(stranger).expect("id");
    assert!(
        detail.validate(&other).is_err(),
        "a detail for another pair is refused"
    );

    let mut action: MatchActionRequest =
        serde_json::from_str(&fixture_text("match_action_request.json")).expect("action");
    action.reason = Some("   ".into());
    assert!(action.validate().is_err(), "a blank reason is refused");
    action.reason = Some("x".repeat(2001));
    assert!(action.validate().is_err(), "an endless reason is refused");
}

#[test]
fn latino_fixtures_round_trip_and_pass_validation() {
    round_trip::<LatinoStartRequest>("latino_start_request.json");
    let run_query: LatinoRunListQuery = round_trip("latino_run_list_query.json");
    run_query.validate().expect("fixture query is valid");
    let runs: LatinoRunPage = round_trip("latino_run_page.json");
    runs.validate(&run_query)
        .expect("fixture run page matches its query");
    assert!(
        runs.items
            .iter()
            .any(|run| run.stats.verified > 0 && run.stats.affinity > 0),
        "the synthetic situations reach ownership and market affinity"
    );

    let detail: LatinoDetail = round_trip("latino_detail.json");
    detail
        .validate(&detail.business_id.clone())
        .expect("fixture detail describes its business");
    let effective = detail
        .effective
        .as_ref()
        .expect("the business shows a review");
    assert_eq!(effective.assessment.method, AssessmentMethod::ManualReview);
    let automatic = detail.automatic.as_ref().expect("the calculation is kept");
    assert!(
        automatic
            .warnings
            .iter()
            .any(|note| note.code == "contradicted"),
        "a contradiction stays visible"
    );
    assert!(
        detail
            .evidence
            .iter()
            .any(|entry| entry.evidence.value.status == EvidenceStatus::Contradicted)
    );
    assert_eq!(detail.decisions.len(), 1);

    let review: LatinoReviewRequest = round_trip("latino_review_request.json");
    review.validate().expect("fixture review is valid");
    assert_eq!(review.business_id, detail.business_id);

    let query: BusinessListQuery = round_trip("latino_business_list_query.json");
    query.validate().expect("fixture query is valid");
    let page: BusinessPage = round_trip("latino_business_page.json");
    page.validate(&query)
        .expect("fixture page obeys the Latino filters");
    assert!(!page.items.is_empty());
}

#[test]
fn latino_payloads_that_claim_more_than_their_evidence_are_rejected() {
    let original: Value = serde_json::from_str(&fixture_text("latino_detail.json")).expect("JSON");
    let mut surname = original.clone();
    surname["automatic"]["assessment"]["status"] = "latino_by_surname".into();
    assert!(serde_json::from_value::<LatinoDetail>(surname).is_err());

    // a verified status with no ownership evidence behind it is refused before the interface
    let mut inflated = original.clone();
    inflated["effective"]["assessment"]["status"] = "verified_latino_owned".into();
    let without_ownership: Vec<Value> = inflated["evidence"]
        .as_array()
        .expect("evidence list")
        .iter()
        .filter(|entry| entry["track"] != "ownership")
        .cloned()
        .collect();
    inflated["evidence"] = Value::Array(without_ownership);
    let inflated: LatinoDetail = serde_json::from_value(inflated).expect("still a detail");
    assert!(inflated.validate(&inflated.business_id.clone()).is_err());

    let stranger = "01900000-0000-7000-8000-000000009999";
    let detail: LatinoDetail = serde_json::from_value(original.clone()).expect("detail");
    assert!(detail.validate(&Id::parse(stranger).expect("id")).is_err());
    let mut foreign = original;
    foreign["history"][0]["assessment"]["business_id"] = stranger.into();
    let foreign: LatinoDetail = serde_json::from_value(foreign).expect("still a detail");
    assert!(foreign.validate(&foreign.business_id.clone()).is_err());

    let mut review: LatinoReviewRequest =
        serde_json::from_str(&fixture_text("latino_review_request.json")).expect("review");
    review.reason = "   ".into();
    assert!(review.validate().is_err(), "a review needs a reason");
    review.reason = "x".repeat(2001);
    assert!(review.validate().is_err(), "an endless reason is refused");

    // a page that shows rows its Latino filters exclude
    let query: BusinessListQuery =
        serde_json::from_str(&fixture_text("latino_business_list_query.json")).expect("query");
    let page: BusinessPage =
        serde_json::from_str(&fixture_text("latino_business_page.json")).expect("page");
    let mut verified_only = query.clone();
    verified_only.latino_status = Some(LatinoStatus::VerifiedLatinoOwned);
    assert!(page.validate(&verified_only).is_err());
    let mut reviewed = query;
    reviewed.latino_review = Some(LatinoReviewFilter::Reviewed);
    assert!(page.validate(&reviewed).is_err());
}

#[test]
fn website_fixtures_round_trip_and_pass_validation() {
    let start: WebsiteStartRequest = round_trip("website_start_request.json");
    start.validate().expect("fixture request is valid");
    let run_query: WebsiteRunListQuery = round_trip("website_run_list_query.json");
    run_query.validate().expect("fixture query is valid");
    let runs: WebsiteRunPage = round_trip("website_run_page.json");
    runs.validate(&run_query)
        .expect("fixture run page matches its query");
    assert!(
        runs.items
            .iter()
            .any(|run| run.stats.audited > 0 && run.stats.unchanged > 0),
        "a second run audits only what changed"
    );

    let query: WebsiteAuditListQuery = round_trip("website_audit_list_query.json");
    query.validate().expect("fixture query is valid");
    let page: WebsiteAuditPage = round_trip("website_audit_page.json");
    page.validate(&query)
        .expect("fixture page matches its query");
    assert!(
        page.items
            .iter()
            .any(|item| item.status == WebsiteAuditStatus::Complete)
    );
    assert!(
        page.items
            .iter()
            .any(|item| item.status == WebsiteAuditStatus::Empty),
        "sites without stored pages are audited as empty"
    );

    let audit: WebsiteAuditQuery = round_trip("website_audit_query.json");
    let detail: WebsiteAuditDetail = round_trip("website_audit_detail.json");
    detail
        .validate(&audit.audit_id)
        .expect("fixture detail describes its audit");
    let diff = detail.diff.as_ref().expect("the site was crawled again");
    assert_eq!(diff.technologies_added, ["google_analytics_4"]);
    assert_eq!(detail.history.len(), 2);
    assert!(
        detail
            .findings
            .iter()
            .any(|finding| finding.result == WebsiteFindingResult::Unknown)
    );
}

#[test]
fn website_payloads_that_do_not_add_up_are_rejected() {
    let original: Value =
        serde_json::from_str(&fixture_text("website_audit_detail.json")).expect("JSON");
    let stranger = Id::parse("01900000-0000-7000-8000-000000009999").expect("id");
    let detail: WebsiteAuditDetail = serde_json::from_value(original.clone()).expect("detail");
    assert!(detail.validate(&stranger).is_err(), "another audit");

    // an unknown result reported as a failure no longer matches its subscore
    let mut recounted = original.clone();
    let unknown = recounted["findings"]
        .as_array_mut()
        .expect("findings")
        .iter_mut()
        .find(|finding| finding["result"] == "unknown")
        .expect("an unknown finding");
    unknown["result"] = "fail".into();
    let recounted: WebsiteAuditDetail = serde_json::from_value(recounted).expect("still a detail");
    assert!(recounted.validate(&recounted.audit.id.clone()).is_err());

    let mut invented = original.clone();
    invented["findings"][0]["result"] = "probably_fine".into();
    assert!(serde_json::from_value::<WebsiteAuditDetail>(invented).is_err());

    let mut foreign = original.clone();
    foreign["history"][1]["site"] = "synthetic-other.example".into();
    let foreign: WebsiteAuditDetail = serde_json::from_value(foreign).expect("still a detail");
    assert!(foreign.validate(&foreign.audit.id.clone()).is_err());

    let mut orphan = original;
    orphan["diff"]["previous_audit_id"] = stranger.to_string().into();
    let orphan: WebsiteAuditDetail = serde_json::from_value(orphan).expect("still a detail");
    assert!(orphan.validate(&orphan.audit.id.clone()).is_err());

    let mut start: WebsiteStartRequest =
        serde_json::from_str(&fixture_text("website_start_request.json")).expect("start");
    start.project_id = None;
    assert!(start.validate().is_err(), "sites need their project");

    let query: WebsiteAuditListQuery =
        serde_json::from_str(&fixture_text("website_audit_list_query.json")).expect("query");
    let mut page: WebsiteAuditPage =
        serde_json::from_str(&fixture_text("website_audit_page.json")).expect("page");
    page.items.push(page.items[0].clone());
    page.total += 1;
    assert!(page.validate(&query).is_err(), "a site listed twice");
}

#[test]
fn contact_fixtures_round_trip_and_pass_validation() {
    let start: ContactStartRequest = round_trip("contact_start_request.json");
    assert!(!start.check_mail_domains);
    let run_query: ContactRunListQuery = round_trip("contact_run_list_query.json");
    run_query.validate().expect("fixture query is valid");
    let runs: ContactRunPage = round_trip("contact_run_page.json");
    runs.validate(&run_query)
        .expect("fixture run page matches its query");
    assert!(
        runs.items
            .iter()
            .any(|run| run.stats.hypotheses > 0 && run.stats.mail_domains_checked > 0)
    );

    let query: ContactListQuery = round_trip("contact_list_query.json");
    query.validate().expect("fixture query is valid");
    let page: ContactPage = round_trip("contact_page.json");
    page.validate(&query)
        .expect("fixture page matches its query");
    assert!(page.items.iter().any(|item| item.suppressed));
    assert!(
        page.items
            .iter()
            .any(|item| item.origin == ContactOrigin::Hypothesis)
    );

    let detail: ContactDetail = round_trip("contact_detail.json");
    detail
        .validate(&detail.business_id.clone())
        .expect("fixture detail describes its business");
    assert!(!detail.hypotheses.is_empty() && !detail.decision_makers.is_empty());
    assert!(
        detail
            .channels
            .iter()
            .any(|channel| channel.validation_status == ValidationStatus::LikelyValid)
    );
    let action: ContactActionRequest = round_trip("contact_action_request.json");
    action.validate().expect("fixture action is valid");

    let people_query: DecisionMakerListQuery = round_trip("decision_maker_list_query.json");
    people_query.validate().expect("fixture query is valid");
    let people: DecisionMakerPage = round_trip("decision_maker_page.json");
    people
        .validate(&people_query)
        .expect("fixture people match their query");
    assert!(
        people
            .items
            .iter()
            .any(|item| item.decision_maker.review_status == DecisionMakerReview::Ambiguous)
    );
    let review: DecisionMakerReviewRequest = round_trip("decision_maker_review_request.json");
    review.validate().expect("fixture review is valid");

    let suppression_query: SuppressionListQuery = round_trip("suppression_list_query.json");
    suppression_query
        .validate()
        .expect("fixture query is valid");
    let suppressions: SuppressionPage = round_trip("suppression_page.json");
    suppressions
        .validate(&suppression_query)
        .expect("fixture suppressions match their query");
    let change: SuppressionChangeRequest = round_trip("suppression_change_request.json");
    change.validate().expect("fixture change is valid");
    let import: SuppressionImportRequest = round_trip("suppression_import_request.json");
    import.validate().expect("fixture import is valid");
    let imported: SuppressionImportResult = round_trip("suppression_import_result.json");
    imported.validate().expect("fixture import result adds up");
    assert_eq!(imported.problems.len(), 1);
    round_trip::<SuppressionExportRequest>("suppression_export_request.json");
    let export: SuppressionExport = round_trip("suppression_export.json");
    export.validate().expect("the export is a safe CSV");
    assert_eq!(export.rows, suppressions.total);
}

#[test]
fn contact_payloads_that_claim_too_much_are_rejected() {
    let original: Value = serde_json::from_str(&fixture_text("contact_detail.json")).expect("JSON");
    let stranger = Id::parse("01900000-0000-7000-8000-000000009999").expect("id");
    let detail: ContactDetail = serde_json::from_value(original.clone()).expect("detail");
    assert!(detail.validate(&stranger).is_err(), "another business");

    let rejected = |mutate: &dyn Fn(&mut Value)| {
        let mut changed = original.clone();
        mutate(&mut changed);
        let changed: ContactDetail = serde_json::from_value(changed).expect("still a detail");
        changed.validate(&changed.business_id.clone()).is_err()
    };
    assert!(
        rejected(&|detail| detail["hypotheses"][0]["validation_status"] = "likely_valid".into()),
        "a hypothesis dressed up as validated"
    );
    assert!(
        rejected(&|detail| detail["hypotheses"][0]["is_primary"] = true.into()),
        "a hypothesis shown as the business's address"
    );
    assert!(
        rejected(&|detail| {
            let hypothesis = detail["hypotheses"][0].clone();
            detail["channels"]
                .as_array_mut()
                .expect("channels")
                .push(hypothesis);
        }),
        "a hypothesis listed among contacts"
    );
    assert!(
        rejected(&|detail| detail["channels"][0]["validation_status"] = "suppressed".into()),
        "suppressed without an entry"
    );
    assert!(
        rejected(&|detail| detail["contactability"]["score"] = 100.into()),
        "a score its components do not add up to"
    );
    let mut invented = original;
    invented["channels"][0]["validation_status"] = "verified".into();
    assert!(serde_json::from_value::<ContactDetail>(invented).is_err());

    let query: ContactListQuery =
        serde_json::from_str(&fixture_text("contact_list_query.json")).expect("query");
    let page: ContactPage = serde_json::from_str(&fixture_text("contact_page.json")).expect("page");
    let mut without_hypotheses = query.clone();
    without_hypotheses.include_hypotheses = false;
    assert!(
        page.validate(&without_hypotheses).is_err(),
        "hypotheses only when asked"
    );
    let mut without_suppressed = query;
    without_suppressed.include_suppressed = false;
    assert!(
        page.validate(&without_suppressed).is_err(),
        "suppressed only when asked"
    );

    let mut export: SuppressionExport =
        serde_json::from_str(&fixture_text("suppression_export.json")).expect("export");
    export
        .content
        .push_str("email,=cmd@synthetic.example,x,manual,2026-01-01T00:00:00.000Z,\n");
    export.rows += 1;
    assert!(export.validate().is_err(), "a cell a spreadsheet would run");

    let mut unexplained: ContactActionRequest =
        serde_json::from_str(&fixture_text("contact_action_request.json")).expect("action");
    unexplained.reason = None;
    assert!(
        unexplained.validate().is_err(),
        "a confirmation needs a reason"
    );
}

#[test]
fn ai_fixtures_round_trip_and_pass_validation() {
    let status_query: AiStatusQuery = round_trip("ai_status_query.json");
    let status: AiStatus = round_trip("ai_status.json");
    status
        .validate(&status_query)
        .expect("fixture status is consistent");
    assert!(status.tasks.iter().any(|task| task.available));
    assert!(!status.recommendations.is_empty() && !status.benchmark_results.is_empty());
    let configure: AiConfigureRequest = round_trip("ai_configure_request.json");
    configure.validate().expect("fixture settings are valid");
    let start: AiStartRequest = round_trip("ai_start_request.json");
    start.validate().expect("fixture start is valid");
    let run_query: AiRunListQuery = round_trip("ai_run_list_query.json");
    let runs: AiRunPage = round_trip("ai_run_page.json");
    runs.validate(&run_query)
        .expect("fixture runs match their query");
    assert!(runs.items[0].stats.latino_candidates_accepted > 0);
    let bench: AiBenchmarkRequest = round_trip("ai_benchmark_request.json");
    bench.validate().expect("fixture benchmark is valid");
    let list_query: AiInferenceListQuery = round_trip("ai_inference_list_query.json");
    let page: AiInferencePage = round_trip("ai_inference_page.json");
    page.validate(&list_query)
        .expect("fixture inferences match their query");
    let query: AiInferenceQuery = round_trip("ai_inference_query.json");
    let detail: AiInferenceDetail = round_trip("ai_inference_detail.json");
    detail
        .validate(&query.inference_run_id)
        .expect("fixture detail is consistent");
    assert_eq!(detail.run.review_status, AiReviewStatus::Accepted);
    let review: AiReviewRequest = round_trip("ai_review_request.json");
    review.validate().expect("fixture review has a reason");
    let change: AiModelChangeRequest = round_trip("ai_model_change_request.json");
    change.validate().expect("fixture change is confirmed");
}

#[test]
fn ai_payloads_that_claim_too_much_are_rejected() {
    let original: Value =
        serde_json::from_str(&fixture_text("ai_inference_detail.json")).expect("JSON");
    let query: AiInferenceQuery =
        serde_json::from_str(&fixture_text("ai_inference_query.json")).expect("query");
    let rejected = |change: &dyn Fn(&mut Value)| {
        let mut changed = original.clone();
        change(&mut changed);
        let detail: AiInferenceDetail = serde_json::from_value(changed).expect("still well formed");
        detail.validate(&query.inference_run_id).is_err()
    };
    let last = original["outputs"].as_array().expect("outputs").len() - 1;
    assert!(
        rejected(&|detail| {
            detail["outputs"][last]["validated_output_json"]["claims"][0]["evidence_ids"] =
                json!(["F99"])
        }),
        "an answer citing an input it was never given"
    );
    assert!(
        rejected(&|detail| detail["run"]["status"] = "invalid_output".into()),
        "a reviewed answer that never validated"
    );
    assert!(
        rejected(&|detail| detail["run"]["attempts"] = 0.into()),
        "more answers than calls"
    );
    assert!(
        rejected(&|detail| {
            detail["outputs"][last]["validation_errors_json"] = json!({ "errors": ["x"] })
        }),
        "an answer that both validated and failed"
    );

    let status: Value = serde_json::from_str(&fixture_text("ai_status.json")).expect("JSON");
    let unexplained = {
        let mut changed = status.clone();
        changed["tasks"][5]["unavailable_reason"] = Value::Null;
        serde_json::from_value::<AiStatus>(changed).expect("well formed")
    };
    assert!(
        unexplained
            .validate(&AiStatusQuery {
                include_llm_fit: true
            })
            .is_err()
    );
    let unasked: AiStatus = serde_json::from_value(status).expect("well formed");
    assert!(
        unasked
            .validate(&AiStatusQuery {
                include_llm_fit: false
            })
            .is_err(),
        "LLM Fit output nobody asked for"
    );

    let change: Value =
        serde_json::from_str(&fixture_text("ai_model_change_request.json")).expect("JSON");
    for (field, value) in [
        ("confirmation", "synthetic-new"),
        ("model", "../../etc/passwd"),
        ("model", "bad name:tag"),
    ] {
        let mut changed = change.clone();
        changed[field] = value.into();
        let request: AiModelChangeRequest = serde_json::from_value(changed).expect("well formed");
        assert!(request.validate().is_err(), "{field}={value}");
    }
    let start: Value = serde_json::from_str(&fixture_text("ai_start_request.json")).expect("JSON");
    for tasks in [
        json!([]),
        json!(["language_detection", "language_detection"]),
    ] {
        let mut changed = start.clone();
        changed["task_types"] = tasks;
        let request: AiStartRequest = serde_json::from_value(changed).expect("well formed");
        assert!(request.validate().is_err());
    }
    let configure: Value =
        serde_json::from_str(&fixture_text("ai_configure_request.json")).expect("JSON");
    let mut huge = configure.clone();
    huge["profiles"][0]["num_ctx"] = 1_000_000.into();
    assert!(
        serde_json::from_value::<AiConfigureRequest>(huge)
            .expect("well formed")
            .validate()
            .is_err()
    );
}

#[test]
fn scoring_fixtures_round_trip_and_pass_validation() {
    round_trip::<ScoringCatalogQuery>("scoring_catalog_query.json");
    let catalog: ScoringCatalog = round_trip("scoring_catalog.json");
    catalog.validate().expect("fixture catalogue is consistent");
    assert!(
        catalog
            .models
            .iter()
            .any(|m| m.model.key == "lead_intelligence.heuristic")
    );
    let save: IcpSaveRequest = round_trip("icp_save_request.json");
    save.validate().expect("fixture ICP is valid");
    let profile: IcpProfileView = round_trip("icp_profile_view.json");
    profile.validate().expect("fixture profile is consistent");
    let model_save: ScoringModelSaveRequest = round_trip("scoring_model_save_request.json");
    model_save.validate().expect("fixture model is valid");
    let model: ScoringModelView = round_trip("scoring_model_view.json");
    model.validate().expect("fixture model view is consistent");
    let change: ScoringChangeRequest = round_trip("scoring_change_request.json");
    change.validate().expect("fixture change names its version");
    let simulation_request: ScoringSimulationRequest =
        round_trip("scoring_simulation_request.json");
    simulation_request
        .validate()
        .expect("fixture simulation request is valid");
    let simulation: ScoringSimulation = round_trip("scoring_simulation.json");
    simulation
        .validate(&simulation_request)
        .expect("fixture simulation counts every business");
    let start: ScoringStartRequest = round_trip("scoring_start_request.json");
    assert!(start.facts_from_run_id.is_some());
    let runs_query: ScoreRunListQuery = round_trip("score_run_list_query.json");
    runs_query.validate().expect("fixture query is valid");
    let runs: ScoreRunPage = round_trip("score_run_page.json");
    runs.validate(&runs_query)
        .expect("fixture runs match their query");
    assert_eq!(runs.items.len(), 2);
    let leads_query: LeadScoreQuery = round_trip("lead_score_query.json");
    leads_query.validate().expect("fixture query is valid");
    let leads: LeadScorePage = round_trip("lead_score_page.json");
    leads
        .validate(&leads_query)
        .expect("fixture leads are ranked and filtered");
    let scorecard_query: ScorecardQuery = round_trip("scorecard_query.json");
    let detail: LeadScorecardDetail = round_trip("lead_scorecard_detail.json");
    detail
        .validate(&scorecard_query)
        .expect("fixture scorecard is consistent");
    assert!(!detail.outcomes.is_empty() && !detail.decisions.is_empty());
    let compare_query: ScoreCompareQuery = round_trip("score_compare_query.json");
    compare_query.validate().expect("fixture query is valid");
    let comparison: ScoreComparison = round_trip("score_comparison.json");
    comparison
        .validate(&compare_query)
        .expect("fixture comparison counts its top");
    assert!(comparison.same_facts);
    let calibration_query: CalibrationQuery = round_trip("calibration_query.json");
    let report: CalibrationReport = round_trip("calibration_report.json");
    report
        .validate(&calibration_query)
        .expect("fixture report nests its counts");
    assert!(report.weights_are_heuristic);
    let action: LeadActionRequest = round_trip("lead_action_request.json");
    action.validate().expect("fixture outcome is valid");
}

#[test]
fn scoring_payloads_that_claim_too_much_are_rejected() {
    let original: Value =
        serde_json::from_str(&fixture_text("lead_scorecard_detail.json")).expect("JSON");
    let query: ScorecardQuery =
        serde_json::from_str(&fixture_text("scorecard_query.json")).expect("query");
    let rejected = |change: &dyn Fn(&mut Value)| {
        let mut changed = original.clone();
        change(&mut changed);
        let detail: LeadScorecardDetail =
            serde_json::from_value(changed).expect("still well formed");
        detail.validate(&query).is_err()
    };
    assert!(
        original["scorecard"]["total_score"].is_number(),
        "the fixture lead is scored"
    );
    assert!(
        rejected(&|detail| detail["scorecard"]["total_score"] = json!(99.5)),
        "contributions that do not add up to the total"
    );
    assert!(
        rejected(&|detail| detail["scorecard"]["tier"] = "excluded".into()),
        "an excluded lead with a total and no failed gate"
    );
    let known = original["scorecard"]["features"]
        .as_array()
        .expect("features")
        .iter()
        .position(|feature| feature["state"] == "known")
        .expect("a known feature");
    assert!(
        rejected(&|detail| detail["scorecard"]["features"][known]["evidence"] = json!([])),
        "a point without evidence"
    );
    assert!(
        rejected(&|detail| {
            detail["ai_reasons"] = json!([{
                "inference_run_id": "01900000-0000-7000-8000-999999999999",
                "model": "synthetic-text:1b", "problem": "other", "explanation": "invented",
                "evidence": [{
                    "kind": "website_finding", "ref": "01900000-0000-7000-8000-999999999998",
                    "label": "invented", "url": null
                }]
            }])
        }),
        "model wording citing evidence the scorecard does not rest on"
    );

    let page: Value = serde_json::from_str(&fixture_text("lead_score_page.json")).expect("JSON");
    let leads_query: LeadScoreQuery =
        serde_json::from_str(&fixture_text("lead_score_query.json")).expect("query");
    let mut swapped = page.clone();
    swapped["items"].as_array_mut().expect("items").swap(0, 1);
    let swapped: LeadScorePage = serde_json::from_value(swapped).expect("well formed");
    assert!(
        swapped.validate(&leads_query).is_err(),
        "ranks out of order"
    );
    let mut excluded_only = leads_query.clone();
    excluded_only.tier = Some(LeadTier::Excluded);
    let page: LeadScorePage = serde_json::from_value(page).expect("well formed");
    assert!(
        page.validate(&excluded_only).is_err(),
        "leads outside the tier asked for"
    );

    let action: Value =
        serde_json::from_str(&fixture_text("lead_action_request.json")).expect("JSON");
    for (field, value) in [
        ("stage", Value::Null),
        ("lost_reason", json!("no budget")),
        ("reason", json!("  ")),
        ("action", json!("confirm")),
        ("action", json!("record_attempt")),
        ("value_amount", json!(10.0)),
        ("value_currency", json!("USD")),
        ("notes", json!("  ")),
    ] {
        let mut changed = action.clone();
        changed[field] = value;
        let request: LeadActionRequest = serde_json::from_value(changed).expect("well formed");
        assert!(request.validate().is_err(), "{field}");
    }
    let icp: Value = serde_json::from_str(&fixture_text("icp_save_request.json")).expect("JSON");
    let mut heavy = icp.clone();
    heavy["definition"]["geography"]["weight"] = 11.into();
    assert!(
        serde_json::from_value::<IcpSaveRequest>(heavy)
            .expect("well formed")
            .validate()
            .is_err(),
        "a weight above 10"
    );
    let mut twice = icp;
    let offer = twice["definition"]["offers"][0].clone();
    twice["definition"]["offers"]
        .as_array_mut()
        .expect("offers")
        .push(offer);
    assert!(
        serde_json::from_value::<IcpSaveRequest>(twice)
            .expect("well formed")
            .validate()
            .is_err(),
        "an offer key twice"
    );
    let mut unnamed: Value =
        serde_json::from_str(&fixture_text("scoring_change_request.json")).expect("JSON");
    unnamed["version_id"] = Value::Null;
    assert!(
        serde_json::from_value::<ScoringChangeRequest>(unnamed)
            .expect("well formed")
            .validate()
            .is_err(),
        "an activation that does not name its version"
    );
}

#[test]
fn operations_fixtures_round_trip_and_pass_validation() {
    round_trip::<PipelineStartRequest>("pipeline_start_request.json")
        .validate()
        .expect("fixture pipeline request is valid");
    round_trip::<DiscoveryControlRequest>("pipeline_control_request.json");
    round_trip::<DeadLetterRequest>("dead_letter_request.json")
        .validate()
        .expect("fixture dead letter request is valid");
    round_trip::<PipelineRun>("pipeline_run.json")
        .validate()
        .expect("fixture pipeline adds up");
    let pipelines: PipelineListQuery = round_trip("pipeline_list_query.json");
    round_trip::<PipelinePage>("pipeline_page.json")
        .validate(&pipelines)
        .expect("fixture pipeline page matches its query");

    let events: JobEventQuery = round_trip("job_event_query.json");
    round_trip::<JobEventPage>("job_event_page.json")
        .validate(&events)
        .expect("fixture event page matches its query");
    let cleanup: EventCleanupRequest = round_trip("event_cleanup_request.json");
    cleanup.validate().expect("fixture event cleanup is valid");
    round_trip::<EventCleanupResult>("event_cleanup_result.json")
        .validate(&cleanup)
        .expect("fixture event cleanup adds up");

    round_trip::<DatasetSnapshotRequest>("dataset_snapshot_request.json")
        .validate()
        .expect("fixture snapshot request is valid");
    let detail: DatasetSnapshotDetail = round_trip("dataset_snapshot_detail.json");
    let snapshot: DatasetSnapshotQuery = round_trip("dataset_snapshot_query.json");
    detail
        .validate(&snapshot.snapshot_id)
        .expect("fixture snapshot detail adds up");
    let snapshots: DatasetSnapshotListQuery = round_trip("dataset_snapshot_list_query.json");
    round_trip::<DatasetSnapshotPage>("dataset_snapshot_page.json")
        .validate(&snapshots)
        .expect("fixture snapshot page matches its query");
    let diff: DatasetDiffQuery = round_trip("dataset_diff_query.json");
    diff.validate().expect("fixture diff query is valid");
    round_trip::<DatasetDiff>("dataset_diff.json")
        .validate(&diff)
        .expect("fixture diff adds up");
    let retention: DatasetCleanupRequest = round_trip("dataset_cleanup_request.json");
    retention.validate().expect("fixture retention is valid");
    round_trip::<DatasetCleanupResult>("dataset_cleanup_result.json")
        .validate(&retention)
        .expect("fixture retention keeps the newest");

    round_trip::<ExportCatalogQuery>("export_catalog_query.json");
    round_trip::<ExportCatalog>("export_catalog.json")
        .validate()
        .expect("fixture export catalogue is complete");
    round_trip::<ExportPresetSaveRequest>("export_preset_save_request.json")
        .validate()
        .expect("fixture preset is valid");
    let draft: ExportDraft = round_trip("export_draft.json");
    draft.validate().expect("fixture draft is valid");
    round_trip::<ExportPreviewResult>("export_preview_result.json")
        .validate(&draft)
        .expect("fixture preview matches its draft");
    let start: ExportStartRequest = round_trip("export_start_request.json");
    assert_eq!(
        ExportStartRequest::from_draft(draft, start.destination.clone()),
        start
    );
    round_trip::<ExportItem>("export_item.json")
        .validate()
        .expect("fixture export is valid");
    round_trip::<ExportCancelRequest>("export_cancel_request.json");
    let exports: ExportListQuery = round_trip("export_list_query.json");
    round_trip::<ExportPage>("export_page.json")
        .validate(&exports)
        .expect("fixture export page matches its query");
}

#[test]
fn operations_payloads_that_do_not_add_up_are_rejected() {
    let draft: ExportDraft = parsed("export_draft.json");
    let diff: DatasetDiffQuery = parsed("dataset_diff_query.json");
    let retention: DatasetCleanupRequest = parsed("dataset_cleanup_request.json");
    let cleanup: EventCleanupRequest = parsed("event_cleanup_request.json");
    let events: JobEventQuery = parsed("job_event_query.json");
    let snapshot: DatasetSnapshotQuery = parsed("dataset_snapshot_query.json");

    let pipeline: [Mutation; 4] = [
        ("stages out of order", |v| {
            v["stages"].as_array_mut().expect("stages").swap(0, 1)
        }),
        ("counts that do not add up", |v| {
            v["counts"]["queued"] = (v["counts"]["queued"].as_u64().expect("count") + 1).into();
        }),
        ("controls its status does not allow", |v| {
            v["actions"] = json!(["resume", "pause"])
        }),
        ("an open dead letter on a pipeline still running", |v| {
            v["status"] = "running".into();
            v["actions"] = json!(["pause", "cancel"]);
            v["dead_letters"] = json!([{
                "id": "01900000-0000-7000-8000-00000000f001",
                "task_id": v["stages"][0]["task_id"], "stage": "discovery", "attempt": 1,
                "error_code": "boom", "error_message": "boom", "retryable": false,
                "review_status": "open", "created_at": "2026-01-01T00:00:00.000Z",
                "resolved_at": null, "resolved_by": null, "resolution_reason": null
            }]);
        }),
    ];
    for (label, mutate) in pipeline {
        assert!(
            refused::<PipelineRun>(fixture_changed("pipeline_run.json", mutate), |run| run
                .validate()),
            "accepted a pipeline with {label}"
        );
    }

    assert!(refused::<PipelineStartRequest>(
        fixture_changed("pipeline_start_request.json", |v| v["idempotency_key"] =
            "short".into()),
        |request| request.validate()
    ));
    assert!(refused::<DeadLetterRequest>(
        fixture_changed("dead_letter_request.json", |v| v["reason"] = "  ".into()),
        |request| request.validate()
    ));
    assert!(
        refused::<EventCleanupRequest>(
            fixture_changed("event_cleanup_request.json", |v| v["levels"] =
                json!(["error"])),
            |request| request.validate()
        ),
        "error events are never cleaned up"
    );
    assert!(refused::<EventCleanupResult>(
        fixture_changed("event_cleanup_result.json", |v| v["deleted"] = 1.into()),
        |result| result.validate(&cleanup)
    ));
    assert!(
        refused::<JobEventPage>(
            fixture_changed("job_event_page.json", |v| {
                v["items"][0]["event"]["level"] = "error".into();
                v["items"][0]["event"]["error_code"] = Value::Null;
            }),
            |page| page.validate(&events)
        ),
        "an error event without its code"
    );

    assert!(refused::<DatasetSnapshotDetail>(
        fixture_changed("dataset_snapshot_detail.json", |v| {
            v["quality"]["rows"] = (v["quality"]["rows"].as_u64().expect("rows") + 1).into();
        }),
        |detail| detail.validate(&snapshot.snapshot_id)
    ));
    assert!(
        refused::<DatasetDiffQuery>(
            fixture_changed("dataset_diff_query.json", |v| {
                v["after_snapshot_id"] = v["before_snapshot_id"].clone();
            }),
            |query| query.validate()
        ),
        "a snapshot compared with itself"
    );
    let diffs: [Mutation; 2] = [
        ("a wrong truncation flag", |v| v["truncated"] = true.into()),
        ("a change naming no column", |v| {
            v["changed"][0]["fields"] = json!(["business_id"])
        }),
    ];
    for (label, mutate) in diffs {
        assert!(
            refused::<DatasetDiff>(fixture_changed("dataset_diff.json", mutate), |d| d
                .validate(&diff)),
            "accepted a diff with {label}"
        );
    }
    let retained: [Mutation; 2] = [
        ("freed space the deleted versions did not hold", |v| {
            v["freed_bytes"] = 1.into()
        }),
        ("the newest version deleted", |v| {
            let kept = v["kept"][0].take();
            let deleted = v["deleted"][0].take();
            v["kept"][0] = deleted;
            v["kept"][0]["kept_because"] = "latest".into();
            v["deleted"][0] = kept;
            v["deleted"][0]["kept_because"] = Value::Null;
            v["freed_bytes"] = v["deleted"][0]["bytes"].clone();
        }),
    ];
    for (label, mutate) in retained {
        assert!(
            refused::<DatasetCleanupResult>(
                fixture_changed("dataset_cleanup_result.json", mutate),
                |result| result.validate(&retention)
            ),
            "accepted a retention with {label}"
        );
    }

    let drafts: [Mutation; 3] = [
        ("do-not-contact leads included without a reason", |v| {
            v["include_suppressed"] = true.into()
        }),
        ("a query export without its query", |v| {
            v["query"] = Value::Null
        }),
        ("a column twice", |v| {
            v["columns"][1] = v["columns"][0].clone()
        }),
    ];
    for (label, mutate) in drafts {
        assert!(
            refused::<ExportDraft>(fixture_changed("export_draft.json", mutate), |d| d
                .validate()),
            "accepted a draft with {label}"
        );
    }
    let previews: [Mutation; 3] = [
        ("a cell a spreadsheet would run", |v| {
            v["sample"][0][0] = "=HYPERLINK(\"http://x\")".into()
        }),
        ("counts that do not add up", |v| v["exported"] = 20.into()),
        ("a do-not-contact lead left in", |v| {
            v["sample"][0][12] = true.into()
        }),
    ];
    for (label, mutate) in previews {
        assert!(
            refused::<ExportPreviewResult>(
                fixture_changed("export_preview_result.json", mutate),
                |preview| preview.validate(&draft)
            ),
            "accepted a preview with {label}"
        );
    }
    assert!(
        refused::<ExportItem>(
            fixture_changed("export_item.json", |v| v["export"]["status"] =
                "succeeded".into()),
            |item| item.validate()
        ),
        "a written export without its hash"
    );
    assert!(
        refused::<ExportCatalog>(
            fixture_changed("export_catalog.json", |v| {
                v["presets"]
                    .as_array_mut()
                    .expect("presets")
                    .retain(|view| view["preset"]["key"] != "contact_review");
            }),
            |catalog| catalog.validate()
        ),
        "a catalogue without a built-in preset"
    );
}

#[test]
fn every_fixture_is_covered() {
    let dir = common::repo_root().join("packages/shared-types/fixtures");
    let mut names: Vec<String> = fs::read_dir(dir)
        .expect("fixtures dir")
        .map(|entry| {
            entry
                .expect("entry")
                .file_name()
                .to_string_lossy()
                .into_owned()
        })
        .filter(|name| name.ends_with(".json"))
        .collect();
    names.sort();
    assert_eq!(
        names,
        [
            "ai_benchmark_request.json",
            "ai_configure_request.json",
            "ai_inference_detail.json",
            "ai_inference_list_query.json",
            "ai_inference_page.json",
            "ai_inference_query.json",
            "ai_model_change_request.json",
            "ai_review_request.json",
            "ai_run_list_query.json",
            "ai_run_page.json",
            "ai_start_request.json",
            "ai_status.json",
            "ai_status_query.json",
            "bulk_request.json",
            "bulk_result.json",
            "business_cluster_detail.json",
            "business_detail.json",
            "business_list_query.json",
            "business_page.json",
            "calibration_query.json",
            "calibration_report.json",
            "campaign_list_query.json",
            "campaign_page.json",
            "campaign_saved.json",
            "candidate_detail.json",
            "candidate_list_query.json",
            "candidate_page.json",
            "canonical_choice_request.json",
            "checklist_catalog.json",
            "checklist_catalog_query.json",
            "checklist_change_request.json",
            "checklist_save_request.json",
            "contact_action_request.json",
            "contact_detail.json",
            "contact_list_query.json",
            "contact_page.json",
            "contact_run_list_query.json",
            "contact_run_page.json",
            "contact_start_request.json",
            "crawl_run_list_query.json",
            "crawl_run_page.json",
            "crawl_start_request.json",
            "crawl_url_detail.json",
            "crawl_url_list_query.json",
            "crawl_url_page.json",
            "criteria_check.json",
            "dataset_cleanup_request.json",
            "dataset_cleanup_result.json",
            "dataset_diff.json",
            "dataset_diff_query.json",
            "dataset_list_query.json",
            "dataset_page.json",
            "dataset_snapshot_detail.json",
            "dataset_snapshot_list_query.json",
            "dataset_snapshot_page.json",
            "dataset_snapshot_query.json",
            "dataset_snapshot_request.json",
            "dead_letter_request.json",
            "decision_maker_list_query.json",
            "decision_maker_page.json",
            "decision_maker_review_request.json",
            "diagnostics_report.json",
            "diagnostics_report.worker_down.json",
            "discovery_run_list_query.json",
            "discovery_run_page.json",
            "discovery_start_request.json",
            "envelope.event.ready.json",
            "envelope.request.health.json",
            "envelope.result.diagnostics.json",
            "envelope.result.error.json",
            "event_cleanup_request.json",
            "event_cleanup_result.json",
            "evidence_detail.json",
            "evidence_query.json",
            "explorer_catalog.json",
            "explorer_catalog_query.json",
            "explorer_page.json",
            "explorer_query.json",
            "export_cancel_request.json",
            "export_catalog.json",
            "export_catalog_query.json",
            "export_draft.json",
            "export_item.json",
            "export_list_query.json",
            "export_page.json",
            "export_preset_save_request.json",
            "export_preview.json",
            "export_preview_request.json",
            "export_preview_result.json",
            "export_start_request.json",
            "icp_profile_view.json",
            "icp_save_request.json",
            "import_preview.json",
            "job_event_page.json",
            "job_event_query.json",
            "latino_business_list_query.json",
            "latino_business_page.json",
            "latino_detail.json",
            "latino_review_request.json",
            "latino_run_list_query.json",
            "latino_run_page.json",
            "latino_start_request.json",
            "lead_action_request.json",
            "lead_compare_query.json",
            "lead_comparison.json",
            "lead_score_page.json",
            "lead_score_query.json",
            "lead_scorecard_detail.json",
            "match_action_request.json",
            "match_detail.json",
            "match_list_query.json",
            "match_page.json",
            "pipeline_control_request.json",
            "pipeline_list_query.json",
            "pipeline_page.json",
            "pipeline_run.json",
            "pipeline_start_request.json",
            "research_action_request.json",
            "research_workspace.json",
            "research_workspace_query.json",
            "resolution_run_list_query.json",
            "resolution_run_page.json",
            "resolution_start_request.json",
            "sales_brief.json",
            "sales_brief_query.json",
            "saved_view_change_request.json",
            "saved_view_item.json",
            "saved_view_list.json",
            "saved_view_query.json",
            "saved_view_save_request.json",
            "score_compare_query.json",
            "score_comparison.json",
            "score_run_list_query.json",
            "score_run_page.json",
            "scorecard_query.json",
            "scoring_catalog.json",
            "scoring_catalog_query.json",
            "scoring_change_request.json",
            "scoring_model_save_request.json",
            "scoring_model_view.json",
            "scoring_simulation.json",
            "scoring_simulation_request.json",
            "scoring_start_request.json",
            "source_catalog.json",
            "suppression_change_request.json",
            "suppression_export.json",
            "suppression_export_request.json",
            "suppression_import_request.json",
            "suppression_import_result.json",
            "suppression_list_query.json",
            "suppression_page.json",
            "synthetic_seed_result.json",
            "timeline_page.json",
            "timeline_query.json",
            "ui_error_report.json",
            "website_audit_detail.json",
            "website_audit_list_query.json",
            "website_audit_page.json",
            "website_audit_query.json",
            "website_run_list_query.json",
            "website_run_page.json",
            "website_start_request.json",
        ],
        "a new fixture needs a round-trip assertion above"
    );
}

#[test]
fn frames_from_the_worker_are_validated() {
    let valid = fixture_text("envelope.result.diagnostics.json");
    let compact: Value = serde_json::from_str(&valid).expect("valid JSON");
    let frame = serde_json::to_vec(&compact).expect("serializable");
    Envelope::from_frame(&frame).expect("fixture frame is valid");

    let mut newer = compact.clone();
    newer["protocol_version"] = 2.into();
    let mut extra = compact;
    extra["unexpected"] = true.into();
    for bad in [newer, extra] {
        let frame = serde_json::to_vec(&bad).expect("serializable");
        assert!(Envelope::from_frame(&frame).is_err());
    }
}

fn parsed<T: DeserializeOwned>(name: &str) -> T {
    serde_json::from_str(&fixture_text(name)).unwrap_or_else(|e| panic!("{name}: {e}"))
}

fn fixture_changed(name: &str, change: impl FnOnce(&mut Value)) -> Value {
    let mut value: Value = serde_json::from_str(&fixture_text(name)).expect("valid JSON");
    change(&mut value);
    value
}

/// Whether a payload is refused, while reading it or by its own checks.
fn refused<T: DeserializeOwned>(value: Value, check: impl Fn(&T) -> Result<(), String>) -> bool {
    match serde_json::from_value::<T>(value) {
        Ok(typed) => check(&typed).is_err(),
        Err(_) => true,
    }
}

#[test]
fn lead_explorer_fixtures_round_trip_and_pass_validation() {
    round_trip::<ExplorerCatalogQuery>("explorer_catalog_query.json");
    let catalog: ExplorerCatalog = round_trip("explorer_catalog.json");
    catalog
        .validate()
        .expect("fixture catalogue matches this build");
    let query: ExplorerQuery = round_trip("explorer_query.json");
    query.validate().expect("fixture query is valid");
    let page: ExplorerPage = round_trip("explorer_page.json");
    page.validate(&query)
        .expect("fixture window answers its query");
    assert!(!page.rows.is_empty() && page.total.is_some());
    let export_request: ExportPreviewRequest = round_trip("export_preview_request.json");
    export_request
        .validate()
        .expect("fixture export request is valid");
    let export: ExportPreview = round_trip("export_preview.json");
    export
        .validate(&export_request)
        .expect("fixture preview adds up");
    let bulk_request: BulkRequest = round_trip("bulk_request.json");
    bulk_request
        .validate()
        .expect("fixture bulk request is valid");
    let bulk: BulkResult = round_trip("bulk_result.json");
    bulk.validate(&bulk_request)
        .expect("fixture bulk result adds up");
    let compare_query: LeadCompareQuery = round_trip("lead_compare_query.json");
    compare_query
        .validate()
        .expect("fixture comparison query is valid");
    let comparison: LeadComparison = round_trip("lead_comparison.json");
    comparison
        .validate(&compare_query)
        .expect("fixture comparison answers its query");
    let views_query: SavedViewQuery = round_trip("saved_view_query.json");
    let views: SavedViewList = round_trip("saved_view_list.json");
    views
        .validate(&views_query)
        .expect("fixture views are consistent");
    assert!(
        views.items.iter().any(|item| item.upgraded_from == Some(1)),
        "the view saved with schema 1 reads upgraded"
    );
    let save: SavedViewSaveRequest = round_trip("saved_view_save_request.json");
    save.validate().expect("fixture save request is valid");
    let item: SavedViewItem = round_trip("saved_view_item.json");
    item.validate().expect("fixture view is consistent");
    let change: SavedViewChangeRequest = round_trip("saved_view_change_request.json");
    change.validate().expect("fixture change request is valid");
    round_trip::<ResearchWorkspaceQuery>("research_workspace_query.json");
    let workspace: ResearchWorkspace = round_trip("research_workspace.json");
    workspace
        .validate()
        .expect("fixture workspace is consistent");
    assert!(
        workspace.research.is_some() && !workspace.pins.is_empty() && !workspace.tasks.is_empty()
    );
    let action: ResearchActionRequest = round_trip("research_action_request.json");
    action.validate().expect("fixture action is valid");
    let timeline_query: TimelineQuery = round_trip("timeline_query.json");
    timeline_query
        .validate()
        .expect("fixture timeline query is valid");
    let timeline: TimelinePage = round_trip("timeline_page.json");
    timeline
        .validate(&timeline_query)
        .expect("fixture timeline runs newest first");
    let evidence_query: EvidenceQuery = round_trip("evidence_query.json");
    evidence_query
        .validate()
        .expect("fixture evidence query is valid");
    let evidence: EvidenceDetail = round_trip("evidence_detail.json");
    evidence
        .validate(&evidence_query)
        .expect("fixture evidence is the row asked for");
    let checklists_query: ChecklistCatalogQuery = round_trip("checklist_catalog_query.json");
    let checklists: ChecklistCatalog = round_trip("checklist_catalog.json");
    checklists
        .validate(&checklists_query)
        .expect("fixture checklists are consistent");
    let save_checklist: ChecklistSaveRequest = round_trip("checklist_save_request.json");
    save_checklist
        .validate()
        .expect("fixture checklist is valid");
    let change_checklist: ChecklistChangeRequest = round_trip("checklist_change_request.json");
    change_checklist
        .validate()
        .expect("fixture activation is valid");
    let brief_query: SalesBriefQuery = round_trip("sales_brief_query.json");
    let brief: SalesBrief = round_trip("sales_brief.json");
    brief
        .validate(&brief_query)
        .expect("fixture brief cites its evidence");
    assert_eq!(brief.sections.len(), 10);
}

#[test]
fn lead_explorer_payloads_that_claim_too_much_are_rejected() {
    let condition = |field: &str, operator: &str, value: Value| json!({ "kind": "condition", "field": field, "operator": operator, "value": value });
    let group = |children: Vec<Value>| json!({ "kind": "group", "join": "and", "negate": false, "children": children });
    let rank = condition("rank", "gt", json!(1.0));
    let trees = [
        (
            group(vec![condition("tier", "gt", json!(1.0))]),
            "an operator the field does not take",
        ),
        (
            group(vec![condition("tier", "eq", json!("gold"))]),
            "a value the field does not have",
        ),
        (
            group(vec![condition("total_score", "gt", json!("high"))]),
            "text where a number goes",
        ),
        (
            group(vec![condition("suppressed", "is_true", json!(true))]),
            "a value for a unary test",
        ),
        (
            group(vec![condition("tier", "in", json!("priority"))]),
            "one value where a list goes",
        ),
        (
            group(vec![group(vec![group(vec![group(vec![])])])]),
            "four levels deep",
        ),
        (
            group(vec![
                group(vec![rank.clone(); 20]),
                group(vec![rank.clone(); 20]),
                rank.clone(),
            ]),
            "forty-one conditions",
        ),
        (group(vec![rank.clone(); 21]), "twenty-one children"),
    ];
    for (tree, why) in trees {
        let query = fixture_changed("explorer_query.json", |q| q["filters"] = tree);
        assert!(refused(query, ExplorerQuery::validate), "{why}");
    }
    for (field, value, why) in [
        (
            "sort",
            json!([{ "field": "tag", "direction": "asc" }]),
            "a sort on a set",
        ),
        (
            "sort",
            json!([{ "field": "rank", "direction": "asc" }, { "field": "rank", "direction": "desc" }]),
            "a field sorted twice",
        ),
        ("offset", json!(5_000_001), "beyond the deepest window"),
        ("limit", json!(0), "an empty window"),
    ] {
        let query = fixture_changed("explorer_query.json", |q| q[field] = value);
        assert!(refused(query, ExplorerQuery::validate), "{why}");
    }

    let query: ExplorerQuery = parsed("explorer_query.json");
    let page = |change: &dyn Fn(&mut Value)| {
        refused(
            fixture_changed("explorer_page.json", |p| change(p)),
            |p: &ExplorerPage| p.validate(&query),
        )
    };
    assert!(
        page(&|p: &mut Value| p["total"] = Value::Null),
        "a window that forgot to count"
    );
    assert!(
        page(&|p: &mut Value| {
            let row = p["rows"][0].clone();
            p["rows"] = json!(vec![row; 6]);
        }),
        "more rows than asked for"
    );
    assert!(
        page(&|p: &mut Value| {
            p["rows"][0]["main_channel"] = json!("phone");
            p["rows"][0]["channel_status"] = json!("likely_valid");
            p["rows"][0]["usable_channels"] = json!(0);
        }),
        "a channel it cannot use"
    );

    let export_request: ExportPreviewRequest = parsed("export_preview_request.json");
    let export = |change: &dyn Fn(&mut Value)| {
        refused(
            fixture_changed("export_preview.json", |p| change(p)),
            |p: &ExportPreview| p.validate(&export_request),
        )
    };
    assert!(
        export(&|p: &mut Value| {
            let exported = p["exported"].as_u64().unwrap_or(0);
            p["exported"] = json!(exported + 1);
        }),
        "counts that do not add up"
    );
    assert!(
        export(&|p: &mut Value| p["sample"][0]["suppressed"] = json!(true)),
        "a suppressed lead in a sample that leaves them out"
    );
    assert!(
        export(&|p: &mut Value| p["warnings"] = json!([])),
        "an export that does not say hypotheses stay in the store"
    );

    let query_value: Value = parsed("explorer_query.json");
    for (field, value, why) in [
        ("mode", json!("apply"), "an applied change without a reason"),
        ("tag", Value::Null, "a tag action without its tag"),
        ("query", query_value, "leads chosen by id and by query"),
    ] {
        let request = fixture_changed("bulk_request.json", |r| r[field] = value);
        assert!(refused(request, BulkRequest::validate), "{why}");
    }
    let bulk_request: BulkRequest = parsed("bulk_request.json");
    let bulk = |change: &dyn Fn(&mut Value)| {
        refused(
            fixture_changed("bulk_result.json", |r| change(r)),
            |r: &BulkResult| r.validate(&bulk_request),
        )
    };
    assert!(
        bulk(&|r: &mut Value| r["batch_id"] = json!("01900000-0000-7000-8000-000000000999")),
        "a preview that claims a batch"
    );
    assert!(
        bulk(&|r: &mut Value| r["changed"] = json!(99)),
        "more changes than targets"
    );

    let action = |change: &dyn Fn(&mut Value)| {
        refused(
            fixture_changed("research_action_request.json", |a| change(a)),
            ResearchActionRequest::validate,
        )
    };
    assert!(
        action(&|a: &mut Value| a["reason"] = Value::Null),
        "a status without a reason"
    );
    assert!(
        action(&|a: &mut Value| {
            a["action"] = json!("request_refresh");
            a["scopes"] = json!([]);
        }),
        "a refresh of nothing"
    );
    assert!(
        action(&|a: &mut Value| {
            a["action"] = json!("pin");
            a["ref_kind"] = json!("business");
            a["ref"] = json!("a-row");
            a["note"] = json!("n".repeat(1001));
        }),
        "a pin with a long note"
    );
    assert!(
        action(&|a: &mut Value| a["action"] = json!("delete_everything")),
        "an action that does not exist"
    );

    let original: ResearchWorkspace = parsed("research_workspace.json");
    let allowed = original.sales_ready_allowed;
    let workspace = |change: &dyn Fn(&mut Value)| {
        refused(
            fixture_changed("research_workspace.json", |w| change(w)),
            ResearchWorkspace::validate,
        )
    };
    assert!(
        workspace(&|w: &mut Value| w["sales_ready_allowed"] = json!(!allowed)),
        "sales-ready allowed against its criteria"
    );
    assert!(
        workspace(&|w: &mut Value| w["suppressed"] = json!(true)),
        "the do-not-contact list against its criterion"
    );
    assert!(
        workspace(&|w: &mut Value| {
            let first = w["sales_ready"][0].clone();
            w["sales_ready"][1] = first;
        }),
        "a criterion twice"
    );
    assert!(
        workspace(&|w: &mut Value| {
            w["pins"][0]["pin"]["business_id"] = json!("01900000-0000-7000-8000-000000000999");
        }),
        "a pin of another lead"
    );

    let timeline_query: TimelineQuery = parsed("timeline_query.json");
    let reversed = fixture_changed("timeline_page.json", |t| {
        if let Some(entries) = t["entries"].as_array_mut() {
            entries.reverse();
        }
    });
    assert!(
        refused(reversed, |t: &TimelinePage| t.validate(&timeline_query)),
        "a timeline oldest first"
    );

    let brief_query: SalesBriefQuery = parsed("sales_brief_query.json");
    let brief = |change: &dyn Fn(&mut Value)| {
        refused(
            fixture_changed("sales_brief.json", |b| change(b)),
            |b: &SalesBrief| b.validate(&brief_query),
        )
    };
    assert!(
        brief(&|b: &mut Value| b["sections"][0]["claims"][0]["evidence"] = json!([])),
        "a claim without evidence"
    );
    assert!(
        brief(&|b: &mut Value| {
            b["sections"][0]["claims"] = json!([]);
            b["sections"][0]["empty_reason"] = Value::Null;
        }),
        "a section that says nothing"
    );
    assert!(
        brief(&|b: &mut Value| {
            let first = b["sections"][0].clone();
            b["sections"][1] = first;
        }),
        "a section twice"
    );
    let channel = json!({
        "kind": "contact_channel", "ref": "phone:+15125550100", "label": "phone: +15125550100",
        "url": null
    });
    assert!(
        brief(&|b: &mut Value| {
            b["suppressed"] = json!(true);
            b["sections"][5]["claims"] = json!([{
                "ref": "B90", "text": "Phone: +15125550100.", "is_inference": false,
                "evidence": [channel.clone()]
            }]);
            b["sections"][5]["empty_reason"] = Value::Null;
        }),
        "a contact route around the do-not-contact list"
    );
    assert!(
        brief(&|b: &mut Value| {
            b["draft"] = json!({
                "inference_run_id": "01900000-0000-7000-8000-000000000999",
                "model": "synthetic-text:1b", "review_status": "unreviewed", "finished_at": null,
                "dropped": 0,
                "sections": [{ "section": "evidence", "sentences": [{
                    "text": "An invented claim.", "claims": ["B99"], "evidence": [channel.clone()]
                }]}]
            });
        }),
        "a draft citing a claim the brief does not make"
    );

    let item = |change: &dyn Fn(&mut Value)| {
        refused(
            fixture_changed("saved_view_item.json", |v| change(v)),
            SavedViewItem::validate,
        )
    };
    assert!(
        item(&|v: &mut Value| v["definition"]["schema_version"] = json!(3)),
        "a schema this build does not write"
    );
    assert!(
        item(&|v: &mut Value| {
            let first = v["definition"]["columns"][0].clone();
            v["definition"]["columns"][1] = first;
        }),
        "a column twice"
    );
    assert!(
        item(&|v: &mut Value| v["upgraded_from"] = json!(1)),
        "an upgrade from a schema it does not hold"
    );

    let checklist = |change: &dyn Fn(&mut Value)| {
        refused(
            fixture_changed("checklist_save_request.json", |c| change(c)),
            ChecklistSaveRequest::validate,
        )
    };
    assert!(
        checklist(&|c: &mut Value| c["definition"]["items"][0]["rule"] = Value::Null),
        "an automatic item without its rule"
    );
    assert!(
        checklist(&|c: &mut Value| {
            let first = c["definition"]["items"][0].clone();
            c["definition"]["items"][1] = first;
        }),
        "an item twice"
    );
    let activation = fixture_changed("checklist_change_request.json", |c| {
        c["version_id"] = Value::Null;
    });
    assert!(
        refused(activation, ChecklistChangeRequest::validate),
        "an activation that does not name its version"
    );
}
