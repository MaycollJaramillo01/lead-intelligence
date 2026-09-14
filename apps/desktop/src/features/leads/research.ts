import type { ResearchAction, ResearchActionRequest } from "@lead/shared-types";

/** A research action with every field it does not use left empty, as the contract has them. */
export const researchRequest = (
  businessId: string,
  action: ResearchAction,
  fields: Partial<Omit<ResearchActionRequest, "business_id" | "action">> = {},
): ResearchActionRequest => ({
  business_id: businessId,
  action,
  status: null,
  assignee: null,
  tag: null,
  note: null,
  note_id: null,
  ref_kind: null,
  ref: null,
  task_id: null,
  title: null,
  item_key: null,
  field: null,
  value: null,
  scopes: [],
  reason: null,
  ...fields,
});
