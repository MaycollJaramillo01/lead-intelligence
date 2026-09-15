import type { DiscoveryCriteria } from "@lead/shared-types";
import type { TranslationKey } from "@/lib/i18n";

/**
 * What a person picks instead of typing codes. A type fills in what each source searches on:
 * Overture taxonomy words, matched as whole words by `overture.places` (read from the 2026-08-19
 * release), and NAICS codes for the sources that filter by industry (USAspending).
 */
export interface BusinessType {
  id: string;
  group: BusinessGroup;
  label: TranslationKey;
  categories: readonly string[];
  naics: readonly string[];
}

export const BUSINESS_GROUPS = [
  "construction",
  "auto",
  "food",
  "health",
  "services",
  "retail",
] as const;

export type BusinessGroup = (typeof BUSINESS_GROUPS)[number];

// id, group, Overture taxonomy words, NAICS codes
const ROWS = [
  ["roofing", "construction", ["roofing"], ["238160"]],
  ["construction", "construction", ["contractor", "building_or_construction_service"], ["236"]],
  ["remodeling", "construction", ["remodeling"], ["236118"]],
  ["plumbing", "construction", ["plumbing"], ["238220"]],
  ["hvac", "construction", ["hvac"], ["238220"]],
  ["electrician", "construction", ["electrician"], ["238210"]],
  ["painting", "construction", ["painting"], ["238320"]],
  ["flooring", "construction", ["flooring_contractor"], ["238330"]],
  ["concrete", "construction", ["masonry_concrete"], ["238110", "238140"]],
  ["fencing", "construction", ["fence"], ["238990"]],
  ["handyman", "construction", ["handyman", "carpenter"], []],
  ["landscaping", "construction", ["landscaping"], ["561730"]],
  [
    "cleaning",
    "construction",
    ["home_cleaning", "cleaning_service", "pressure_washing", "window_washing"],
    ["561720"],
  ],
  ["pest_control", "construction", ["pest_control"], ["561710"]],
  ["pool", "construction", ["pool_cleaning"], ["561790"]],
  ["garage_door", "construction", ["garage_door"], ["238290"]],
  ["locksmith", "construction", ["locksmith"], ["561622"]],
  ["junk_removal", "construction", ["junk_removal"], ["562119"]],
  ["auto_repair", "auto", ["automotive_repair", "automotive_service"], ["811111"]],
  ["auto_body", "auto", ["auto_body_shop"], ["811121"]],
  ["tires", "auto", ["tire"], []],
  ["car_wash", "auto", ["car_wash", "auto_detailing"], ["811192"]],
  ["used_cars", "auto", ["used_auto_dealer"], ["441120"]],
  ["towing", "auto", ["towing"], ["488410"]],
  ["trucking", "auto", ["freight", "courier"], ["484"]],
  ["moving", "auto", ["mover"], ["484210"]],
  ["restaurant", "food", ["restaurant"], ["7225"]],
  [
    "mexican_food",
    "food",
    ["mexican_restaurant", "taco_restaurant", "texmex_restaurant"],
    ["7225"],
  ],
  ["food_truck", "food", ["food_truck"], ["722330"]],
  ["bakery", "food", ["bakery"], ["311811"]],
  ["catering", "food", ["caterer"], ["722320"]],
  ["grocery", "food", ["grocery_store", "convenience_store"], ["445110"]],
  ["hair_salon", "health", ["hair_salon", "beauty_salon"], ["812112"]],
  ["barber", "health", ["barber"], ["812111"]],
  ["nail_salon", "health", ["nail_salon"], ["812113"]],
  ["spa", "health", ["spa", "massage"], ["812199"]],
  ["dentist", "health", ["dental_clinic", "general_dentistry"], ["621210"]],
  ["doctor", "health", ["doctors_office", "family_practice"], ["621111"]],
  ["home_health", "health", ["home_health_care"], ["621610"]],
  ["daycare", "health", ["child_care_and_day_care", "day_care_preschool"], ["624410"]],
  ["gym", "health", ["gym", "fitness_trainer"], ["713940"]],
  ["insurance", "services", ["insurance_agency", "auto_insurance"], ["524210"]],
  ["tax", "services", ["tax_service", "accountant"], ["541213", "541211"]],
  ["real_estate", "services", ["real_estate_agent", "real_estate_service"], ["531210"]],
  ["lawyer", "services", ["attorney_or_law_firm", "law"], ["541110"]],
  ["money_transfer", "services", ["money_transfer_service"], ["522390"]],
  ["events", "services", ["party_and_event_planning", "event_venue"], ["812990"]],
  ["photography", "services", ["photography_service"], ["541921"]],
  ["printing", "services", ["printing_service", "sign_making"], ["323111"]],
  ["it_services", "services", ["it_service_and_computer_repair"], ["541512"]],
  ["marketing", "services", ["marketing_agency", "advertising_agency"], ["541810"]],
  ["tailoring", "services", ["sewing_and_alterations", "tailor"], ["811490"]],
  ["clothing", "retail", ["clothing_store", "fashion_boutique"], []],
  ["phone_store", "retail", ["mobile_phone_store"], []],
  ["furniture", "retail", ["furniture_store"], []],
] as const;

export const BUSINESS_TYPES: readonly BusinessType[] = ROWS.map(
  ([id, group, categories, naics]): BusinessType => ({
    id,
    group,
    label: `businessType.${id}`,
    categories,
    naics,
  }),
);

/** A type counts as picked while every one of its taxonomy words is among the categories. */
export function hasBusinessType(criteria: DiscoveryCriteria, type: BusinessType): boolean {
  return type.categories.every((category) => criteria.categories.includes(category));
}

/** Picks or drops a type. Dropping one keeps the words and codes another picked type still needs. */
export function withBusinessType(
  criteria: DiscoveryCriteria,
  type: BusinessType,
  picked: boolean,
): DiscoveryCriteria {
  if (picked) {
    return {
      ...criteria,
      categories: [...new Set([...criteria.categories, ...type.categories])],
      verticals: [...new Set([...criteria.verticals, ...type.naics])],
    };
  }
  const others = BUSINESS_TYPES.filter(
    (other) => other.id !== type.id && hasBusinessType(criteria, other),
  );
  const drop = (values: readonly string[], own: readonly string[], kept: readonly string[]) =>
    values.filter((value) => !own.includes(value) || kept.includes(value));
  return {
    ...criteria,
    categories: drop(
      criteria.categories,
      type.categories,
      others.flatMap((other) => other.categories),
    ),
    verticals: drop(
      criteria.verticals,
      type.naics,
      others.flatMap((other) => other.naics),
    ),
  };
}

/** States by name, so nobody has to remember a two-letter code. */
export const US_STATES: readonly (readonly [code: string, name: string])[] = [
  ["AL", "Alabama"],
  ["AK", "Alaska"],
  ["AZ", "Arizona"],
  ["AR", "Arkansas"],
  ["CA", "California"],
  ["CO", "Colorado"],
  ["CT", "Connecticut"],
  ["DE", "Delaware"],
  ["DC", "District of Columbia"],
  ["FL", "Florida"],
  ["GA", "Georgia"],
  ["HI", "Hawaii"],
  ["ID", "Idaho"],
  ["IL", "Illinois"],
  ["IN", "Indiana"],
  ["IA", "Iowa"],
  ["KS", "Kansas"],
  ["KY", "Kentucky"],
  ["LA", "Louisiana"],
  ["ME", "Maine"],
  ["MD", "Maryland"],
  ["MA", "Massachusetts"],
  ["MI", "Michigan"],
  ["MN", "Minnesota"],
  ["MS", "Mississippi"],
  ["MO", "Missouri"],
  ["MT", "Montana"],
  ["NE", "Nebraska"],
  ["NV", "Nevada"],
  ["NH", "New Hampshire"],
  ["NJ", "New Jersey"],
  ["NM", "New Mexico"],
  ["NY", "New York"],
  ["NC", "North Carolina"],
  ["ND", "North Dakota"],
  ["OH", "Ohio"],
  ["OK", "Oklahoma"],
  ["OR", "Oregon"],
  ["PA", "Pennsylvania"],
  ["RI", "Rhode Island"],
  ["SC", "South Carolina"],
  ["SD", "South Dakota"],
  ["TN", "Tennessee"],
  ["TX", "Texas"],
  ["UT", "Utah"],
  ["VT", "Vermont"],
  ["VA", "Virginia"],
  ["WA", "Washington"],
  ["WV", "West Virginia"],
  ["WI", "Wisconsin"],
  ["WY", "Wyoming"],
];
