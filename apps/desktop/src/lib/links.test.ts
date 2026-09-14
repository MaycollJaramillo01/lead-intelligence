import { describe, expect, it } from "vitest";
import { businessPath, datasetPath, jobPath, scoreRunPath, sourcePath } from "@/lib/links";

describe("deep links", () => {
  it("builds a path for each linked entity, encoding what it carries", () => {
    expect(businessPath("01900000-0000-7000-8000-000000000001")).toBe(
      "/businesses/01900000-0000-7000-8000-000000000001",
    );
    expect(jobPath("01900000-0000-7000-8000-000000000002")).toBe(
      "/jobs?job=01900000-0000-7000-8000-000000000002",
    );
    expect(sourcePath("osm.overpass")).toBe("/sources?source=osm.overpass");
    expect(scoreRunPath("run 1")).toBe("/lead-explorer?run=run+1");
    expect(datasetPath("a&b")).toBe("/datasets?dataset=a%26b");
  });
});
