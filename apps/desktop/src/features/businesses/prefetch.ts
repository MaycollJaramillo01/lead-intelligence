import type { BusinessDetail } from "@lead/shared-types";
import { desktop } from "@/lib/ipc";

/**
 * One business read ahead (phase 12): opening a lead reads the next one of the list, so moving down
 * the list answers at once. Only one is kept, and it is used once.
 */
let ahead: { id: string; detail: Promise<BusinessDetail> } | null = null;

export function prefetchBusiness(businessId: string): void {
  if (ahead?.id === businessId) return;
  const detail = desktop.getBusiness({ business_id: businessId });
  // a failed read ahead is read again when the business opens
  detail.catch(() => {});
  ahead = { id: businessId, detail };
}

export function loadBusiness(businessId: string): Promise<BusinessDetail> {
  if (ahead?.id !== businessId) return desktop.getBusiness({ business_id: businessId });
  const { detail } = ahead;
  ahead = null;
  return detail.catch(() => desktop.getBusiness({ business_id: businessId }));
}
