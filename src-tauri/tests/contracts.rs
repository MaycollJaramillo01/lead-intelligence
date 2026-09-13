//! Round-trips every generated fixture (packages/shared-types/fixtures) through the Rust types.
//! Python produced them from the Pydantic source of truth; equality after Rust
//! deserialize -> serialize proves both sides agree on every field, enum value and null.

mod common;

use std::fs;

use lead_intelligence_lib::data::{
    AssessmentMethod, BusinessDetail, BusinessListQuery, BusinessPage, CampaignListQuery,
    CampaignPage, CampaignSaved, CandidateDetail, CandidateListQuery, CandidatePage,
    CanonicalChoiceRequest, CrawlRunListQuery, CrawlRunPage, CrawlStartRequest, CrawlUrlDetail,
    CrawlUrlListQuery, CrawlUrlPage, CrawlUrlStatus, CriteriaCheck, DatasetListQuery, DatasetPage,
    DiscoveryRunListQuery, DiscoveryRunPage, DiscoveryStartRequest, EvidenceStatus, Id,
    ImportPreview, LatinoDetail, LatinoReviewFilter, LatinoReviewRequest, LatinoRunListQuery,
    LatinoRunPage, LatinoStartRequest, LatinoStatus, MatchActionRequest, MatchDetail,
    MatchListQuery, MatchPage, MatchStatus, ResolutionRunListQuery, ResolutionRunPage,
    ResolutionStartRequest, SourceCatalog, SourceHealth, SyntheticSeedResult,
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
            "business_cluster_detail.json",
            "business_detail.json",
            "business_list_query.json",
            "business_page.json",
            "campaign_list_query.json",
            "campaign_page.json",
            "campaign_saved.json",
            "candidate_detail.json",
            "candidate_list_query.json",
            "candidate_page.json",
            "canonical_choice_request.json",
            "crawl_run_list_query.json",
            "crawl_run_page.json",
            "crawl_start_request.json",
            "crawl_url_detail.json",
            "crawl_url_list_query.json",
            "crawl_url_page.json",
            "criteria_check.json",
            "dataset_list_query.json",
            "dataset_page.json",
            "diagnostics_report.json",
            "diagnostics_report.worker_down.json",
            "discovery_run_list_query.json",
            "discovery_run_page.json",
            "discovery_start_request.json",
            "envelope.event.ready.json",
            "envelope.request.health.json",
            "envelope.result.diagnostics.json",
            "envelope.result.error.json",
            "import_preview.json",
            "latino_business_list_query.json",
            "latino_business_page.json",
            "latino_detail.json",
            "latino_review_request.json",
            "latino_run_list_query.json",
            "latino_run_page.json",
            "latino_start_request.json",
            "match_action_request.json",
            "match_detail.json",
            "match_list_query.json",
            "match_page.json",
            "resolution_run_list_query.json",
            "resolution_run_page.json",
            "resolution_start_request.json",
            "source_catalog.json",
            "synthetic_seed_result.json",
            "ui_error_report.json",
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
