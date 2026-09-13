import type { LatinoReviewFilter, LatinoStatus, SourceKind } from "@lead/shared-types";

/** The Latino evidence filters of the Businesses list, as its controls hold them. */
export interface LatinoFilter {
  status: LatinoStatus | "";
  /** Percent, as typed. */
  minConfidence: string;
  sourceKind: SourceKind | "";
  review: LatinoReviewFilter | "";
}

export const NO_LATINO_FILTER: LatinoFilter = {
  status: "",
  minConfidence: "",
  sourceKind: "",
  review: "",
};

/** The query fields of a filter; an empty control filters nothing. */
export function latinoQuery(filter: LatinoFilter) {
  return {
    latino_status: filter.status || null,
    latino_min_confidence:
      filter.minConfidence === ""
        ? null
        : Math.min(100, Math.max(0, Number(filter.minConfidence))) / 100,
    latino_source_kind: filter.sourceKind || null,
    latino_review: filter.review || null,
  };
}
