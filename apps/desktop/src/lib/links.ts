/**
 * Deep links between screens (phase 12): a business, the job that produced something, a source, the
 * score run a lead was ranked in, and a dataset. Each screen reads its own parameter.
 */

const withParam = (path: string, name: string, value: string) =>
  `${path}?${new URLSearchParams({ [name]: value }).toString()}`;

export const businessPath = (businessId: string) => `/businesses/${encodeURIComponent(businessId)}`;

export const jobPath = (jobId: string) => withParam("/jobs", "job", jobId);

export const sourcePath = (connectorKey: string) => withParam("/sources", "source", connectorKey);

/** The Lead Explorer over one score run, the version of the scores a lead was ranked with. */
export const scoreRunPath = (scoreRunId: string) => withParam("/lead-explorer", "run", scoreRunId);

export const datasetPath = (datasetId: string) => withParam("/datasets", "dataset", datasetId);
