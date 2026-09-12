import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { EvidenceItem, type EvidenceRecord } from "@/components/domain/EvidenceItem";
import { JobProgress, type JobSummary } from "@/components/domain/JobProgress";
import { ScoreValue, type ScoreRecord } from "@/components/domain/ScoreValue";

/** All values below are synthetic props supplied by the test, never product data. */
const EVIDENCE: EvidenceRecord = {
  id: "e1",
  claim: "Self-declared ownership",
  observedValue: "Family owned since 1998",
  source: { label: "example.test/about", type: "website", url: "https://example.test/about" },
  capturedAt: "2026-01-02T03:04:05Z",
  confidence: 0.82,
  strength: "strong",
  freshness: "fresh",
};

describe("EvidenceItem", () => {
  it("shows the claim with its source, date, confidence and strength in words", () => {
    render(<EvidenceItem evidence={EVIDENCE} />);
    expect(screen.getByText("Self-declared ownership")).toBeTruthy();
    expect(screen.getByText("Family owned since 1998")).toBeTruthy();
    expect(screen.getByText(/Strength: strong/)).toBeTruthy();
    expect(screen.getByText("82%")).toBeTruthy();
    expect(screen.getByRole("link", { name: /example.test\/about/ })).toHaveProperty(
      "href",
      "https://example.test/about",
    );
  });
});

describe("ScoreValue", () => {
  const scored: ScoreRecord = {
    label: "Lead score",
    value: 72,
    modelVersion: "v0.3",
    confidence: "medium",
    factors: [
      { label: "Digital gap", contribution: 18 },
      { label: "Missing contact", contribution: -6, note: "no published email" },
    ],
  };

  it("exposes value, version, confidence and the factor breakdown", async () => {
    const user = userEvent.setup();
    render(<ScoreValue score={scored} />);
    expect(screen.getByText("72")).toBeTruthy();
    expect(screen.getByText(/Version v0.3/)).toBeTruthy();
    expect(screen.getByText("medium")).toBeTruthy();
    const meter = screen.getByRole("progressbar", { name: "Lead score" });
    expect(meter).toHaveProperty("value", 72);

    await user.click(screen.getByText("Why this value"));
    expect(screen.getByText("Digital gap")).toBeTruthy();
    expect(screen.getByText("+18")).toBeTruthy();
    expect(screen.getByText("-6")).toBeTruthy();
  });

  it("says why there is no score instead of showing a zero", () => {
    render(
      <ScoreValue
        score={{
          label: "Lead score",
          value: null,
          modelVersion: "v0.3",
          confidence: null,
          unavailableReason: "no evidence collected yet",
        }}
      />,
    );
    expect(screen.getByText(/Not scored: no evidence collected yet/)).toBeTruthy();
    expect(screen.getByRole("progressbar", { name: "Lead score" })).toHaveProperty("position", -1);
  });
});

describe("JobProgress", () => {
  const running: JobSummary = {
    id: "j1",
    name: "Discovery run",
    state: "running",
    done: 40,
    total: 200,
    ratePerMinute: 12,
    errors: 2,
  };

  it("shows determinate progress with rate and errors, and can be cancelled", async () => {
    const user = userEvent.setup();
    const cancel = vi.fn<(id: string) => void>();
    render(<JobProgress job={running} onCancel={cancel} />);
    expect(screen.getByText("40/200 (20%)")).toBeTruthy();
    expect(screen.getByText("12/min")).toBeTruthy();
    expect(screen.getByText("2")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: /cancel job/i }));
    expect(cancel).toHaveBeenCalledWith("j1");
  });

  it("falls back to indeterminate progress when the total is unknown", () => {
    render(
      <JobProgress job={{ id: "j2", name: "Crawl", state: "running", done: 10, total: null }} />,
    );
    expect(screen.getByText("Working, total unknown")).toBeTruthy();
    expect(screen.getByRole("progressbar", { name: /Crawl/ })).toHaveProperty("position", -1);
  });

  it("does not offer cancellation for finished jobs", () => {
    render(
      <JobProgress
        job={{ id: "j3", name: "Export", state: "succeeded" }}
        onCancel={vi.fn<(id: string) => void>()}
      />,
    );
    expect(screen.queryByRole("button", { name: /cancel job/i })).toBeNull();
    expect(screen.getByText("succeeded")).toBeTruthy();
  });
});
