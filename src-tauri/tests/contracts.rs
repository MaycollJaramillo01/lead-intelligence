//! Round-trips every generated fixture (packages/shared-types/fixtures) through the Rust types.
//! Python produced them from the Pydantic source of truth; equality after Rust
//! deserialize -> serialize proves both sides agree on every field, enum value and null.

mod common;

use std::fs;

use lead_intelligence_lib::data::{
    BusinessDetail, BusinessListQuery, BusinessPage, CampaignListQuery, CampaignPage,
    CampaignSaved, CandidateDetail, CandidateListQuery, CandidatePage, CriteriaCheck,
    DatasetListQuery, DatasetPage, DiscoveryRunListQuery, DiscoveryRunPage, DiscoveryStartRequest,
    ImportPreview, SourceCatalog, SourceHealth, SyntheticSeedResult,
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
            "business_detail.json",
            "business_list_query.json",
            "business_page.json",
            "campaign_list_query.json",
            "campaign_page.json",
            "campaign_saved.json",
            "candidate_detail.json",
            "candidate_list_query.json",
            "candidate_page.json",
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
