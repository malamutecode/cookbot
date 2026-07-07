export type Page = 'chat' | 'calendar' | 'sources' | 'admin'

export interface TokenQuota {
  daily_limit: number    // 0 ⇒ unlimited
  monthly_limit: number  // 0 ⇒ unlimited
}

export interface UserRecord {
  uid: string
  email?: string | null
  role: string
  quota: TokenQuota
  disabled: boolean
}

export interface UserUsageView {
  record: UserRecord
  daily_used: number
  monthly_used: number
}

export interface MeView {
  uid: string
  email?: string | null
  role: string
  is_admin: boolean
}

export interface SpizarniaItem {
  name: string
  quantity: string
  added_at?: string
}

export interface Spizarnia {
  uid: string
  items: SpizarniaItem[]
  updated_at?: string
}

export interface ShopItem {
  name: string
  checked: boolean
  section?: string  // set when coming from ShoppingListAgent
}

export interface ShoppingSection {
  name: string
  items: ShopItem[]
}

export interface Product {
  id: string
  name: string
  category?: string
  brand?: string | null
  price?: number | null
  grammage?: number | null
  unit?: string | null
  url: string
  image_url?: string | null
  available: boolean
}

export interface ProductMatch {
  ingredient: string
  shop: string
  product: Product
  score: number
}

export interface GroceryMatchResult {
  shop: string
  matched: ProductMatch[]
  unmatched: { ingredient: string }[]
  generated_at?: string | null
}

export interface Recipe {
  name: string
  description: string
  ingredients: string[]
  steps: string[]
  prep_time_minutes: number
  cook_time_minutes: number
  difficulty: string
  servings: number
  tips?: string[]
  source_url?: string
  image_url?: string
}

export interface HitlLabels {
  heading: string
  approve: string
  modify: string
  reject: string
  modify_placeholder: string
  modify_send: string
  approved_note: string
  rejected_note: string
  modification_note: string
}

export interface UiStrings {
  greeting?: string
  thinking?: string
  spizarnia_heading?: string
  spizarnia_empty?: string
  spizarnia_toggle?: string
  spizarnia_add_placeholder?: string
  spizarnia_add_button?: string
  shopping_list_heading?: string
  shopping_list_clear?: string
  shopping_list_add_placeholder?: string
  shopping_list_add_button?: string
  shopping_list_organize?: string
  shopping_list_organizing?: string
  shopping_list_copy?: string
  shopping_list_share?: string
  shopping_list_copied?: string
  shopping_list_empty?: string
  frisco_button?: string
  frisco_heading?: string
  frisco_not_found?: string
  frisco_open?: string
  frisco_loading?: string
  frisco_generated_at?: string
  spizarnia_offer_add?: string
  spizarnia_offer_remove?: string
  spizarnia_offer_confirm?: string
  spizarnia_offer_skip?: string
  login_heading?: string
  login_email?: string
  login_password?: string
  login_button?: string
  logout_button?: string
  hitl?: HitlLabels
}

export interface RecipeSummary {
  name: string
  description: string
  difficulty: string
  total_time_minutes: number
  key_ingredients: string[]
  source: string
  source_url?: string
  image_url?: string
}

// WebSocket message types
export type WsOutMessage =
  | { type: 'token'; content: string }
  | { type: 'agent_update'; agent: string; status: string }
  | { type: 'final_recipe'; recipe: Recipe; source: string }
  | { type: 'recipe_options'; proposals: RecipeSummary[] }
  | { type: 'hitl_checkpoint'; recipe: Recipe; round: number; labels: HitlLabels }
  | { type: 'spizarnia_offer'; missing_ingredients: string[]; used_from_spizarnia: string[] }
  | { type: 'calendar_update'; action: 'add' | 'remove'; entry?: CalendarEntry; entry_id?: string }
  | { type: 'shopping_list_update'; items: string[]; replace: boolean; structured?: { items: { name: string; quantity: string; section: string }[]; sections: string[] } }
  | { type: 'quota_exceeded'; window: 'daily' | 'monthly'; message: string; resets_at: string }
  | { type: 'error'; message: string }

export interface CalendarDay {
  date: string // ISO date string YYYY-MM-DD
  recipes: CalendarEntry[]
  freeText: string
}

export interface CalendarEntry {
  id: string
  recipeName: string
  ingredients: string[]
  date?: string    // ISO YYYY-MM-DD — set when added via agent
  recipe?: Recipe  // full recipe for detail view
}
