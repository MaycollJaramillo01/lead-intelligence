//! Round-trips every generated fixture (packages/shared-types/fixtures) through the Rust types.
//! Python produced them from the Pydantic source of truth; equality after Rust
//! deserialize -> serialize proves both sides agree on every field, enum value and null.

mod common;

use std::fs;

use lead_intelligence_lib::data::{
    BusinessDetail, BusinessListQuery, BusinessPage, DatasetListQuery, DatasetPage,
    SyntheticSeedResult,
};
use lead_intelligence_lib::protocol::{
    DATA_CONTRACT_VERSION, DiagnosticsReport, DiagnosticsResult, Envelope, HealthResult,
    UiErrorReport,
};
use serde::Serialize;
use serde::de::DeserializeOwned;
use serde_json::Value;

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
            "dataset_list_query.json",
            "dataset_page.json",
            "diagnostics_report.json",
            "diagnostics_report.worker_down.json",
            "envelope.event.ready.json",
            "envelope.request.health.json",
            "envelope.result.diagnostics.json",
            "envelope.result.error.json",
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
