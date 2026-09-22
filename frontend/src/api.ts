// Thin typed API client. Session + CSRF, same-origin (SPA served by Django).

export class ApiError extends Error {
  status: number
  data: unknown
  constructor(status: number, message: string, data: unknown) {
    super(message)
    this.status = status
    this.data = data
  }
}

function getCookie(name: string): string {
  const match = document.cookie.match(new RegExp('(^|;\\s*)' + name + '=([^;]*)'))
  return match ? decodeURIComponent(match[2]) : ''
}

export async function ensureCsrf(): Promise<void> {
  if (getCookie('csrftoken')) return
  await fetch('/api/auth/csrf/', { credentials: 'same-origin' })
}

export async function api<T = unknown>(path: string, options: RequestInit = {}): Promise<T> {
  const method = (options.method || 'GET').toUpperCase()
  const headers = new Headers(options.headers || {})
  const isForm = options.body instanceof FormData
  if (!isForm && options.body !== undefined) headers.set('Content-Type', 'application/json')
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
    await ensureCsrf()
    headers.set('X-CSRFToken', getCookie('csrftoken'))
  }
  const res = await fetch(path, { ...options, headers, credentials: 'same-origin' })
  const text = await res.text()
  const data = text ? safeJson(text) : null
  if (!res.ok) {
    const detail =
      (data && (data.detail || data.message)) ||
      (data && typeof data === 'object' ? JSON.stringify(data) : text) ||
      res.statusText
    throw new ApiError(res.status, String(detail), data)
  }
  return data as T
}

function safeJson(text: string): any {
  try {
    return JSON.parse(text)
  } catch {
    return text
  }
}

// ---- Types (subset of the API surface the UI touches) ----
export interface Property {
  id: number
  name: string
  address: string
  purchase_date: string | null
  purchase_price: string | null
  total_floor_area_sqm: string | null
  rental_floor_area_sqm: string | null
  let_percentage: string | null
  gst_registered: boolean
  default_depreciation_method: 'diminishing_value' | 'prime_cost'
  notes: string
  let_share: string | null
  rental_area_share: string | null
  ownership_total: string
  ownerships: Ownership[]
}

export interface Ownership {
  id: number
  property: number
  owner: number
  owner_name: string
  share_pct: string
}

export interface Owner { id: number; name: string; email: string; notes: string }

export interface Listing {
  id: number
  property: number
  property_name: string
  platform: string
  name: string
  external_id: string
  url: string
  active: boolean
}

export interface Category {
  id: number
  name: string
  kind: string
  default_apportionment: string
  details: string
}

export interface Expense {
  id: number
  property: number
  property_name: string
  listing: number | null
  category: number
  category_name: string
  kind: 'adhoc' | 'utility'
  date: string
  vendor: string
  description: string
  amount: string
  gst_amount: string
  apportionment: 'none' | 'area' | 'custom'
  apportionment_pct: string | null
  deductible_amount: string
  paid: boolean
  payment_method: string
  notes: string
  source: string
  receipts: Receipt[]
}

export interface Receipt {
  id: number
  property: number
  file: string
  url: string
  original_name: string
  note: string
  expense: number | null
  asset: number | null
  created_at: string
}

export interface UtilityType {
  id: number
  property: number
  name: string
  category: number
  category_name: string
  frequency: 'monthly' | 'quarterly' | 'half_yearly' | 'yearly' | 'other'
  supplier: string
  apportionment: string
  coverage_start: string | null
  notes: string
}

export interface UtilityBill {
  id: number
  utility_type: number
  utility_type_name: string
  bill_date: string | null
  period_start: string | null
  period_end: string | null
  amount: string
  gst_amount: string
  paid: boolean
  attachment: string | null
  attachment_url: string | null
  extracted: Record<string, unknown>
  notes: string
  expense: number | null
  claimable_amount: string | null
}

export interface DepreciationEntry {
  id: number
  financial_year: string
  method: string
  opening_value: string
  deduction: string
  closing_value: string
  days_held: number | null
}

export interface Asset {
  id: number
  property: number
  property_name: string
  name: string
  kind: string
  purchase_date: string
  cost: string
  effective_life_years: string | null
  method: 'diminishing_value' | 'prime_cost'
  business_use_pct: string
  low_value_pool: boolean
  effective_life_is_estimate: boolean
  image: string | null
  image_url: string | null
  disposed_date: string | null
  disposal_value: string | null
  notes: string
  depreciation_entries: DepreciationEntry[]
  receipts: Receipt[]
}

export interface ExpenseExtraction {
  vendor?: string | null
  description?: string | null
  amount?: string | null
  gst_amount?: string | null
  date?: string | null
  category_hint?: string | null
  category?: number
  category_name?: string
  currency?: string
  notes?: string | null
  extracted_by?: string
  needs_ocr?: boolean
  message?: string
  detail?: string
}

export interface AssetExtraction {
  name?: string | null
  supplier?: string | null
  cost?: string | null
  gst_amount?: string | null
  purchase_date?: string | null
  asset_kind?: string | null
  effective_life_years?: string | null
  effective_life_is_estimate?: boolean
  suggested_method?: string | null
  currency?: string
  notes?: string | null
  extracted_by?: string
  needs_ocr?: boolean
  message?: string
  detail?: string
}

export interface MonthlyEarnings {
  id: number
  listing: number
  listing_name: string
  month: string
  currency: string
  gross_earnings: string
  service_fees: string
  tax_withheld: string
  total_earnings: string
  source: string
}

export interface Reservation {
  id: number
  listing: number
  listing_name: string
  confirmation_code: string
  guest_name: string
  check_in: string | null
  check_out: string | null
  nights: number | null
  currency: string
  accommodation_amount: string
  cleaning_fee: string
  gross_earnings: string
  airbnb_fee: string
  taxes_collected: string
  net_payout: string
  payout_date: string | null
  status: string
  source: string
}

export interface EarningsSummary {
  id: number
  listing: number
  listing_name: string
  financial_year: string
  period_start: string | null
  period_end: string | null
  nights_booked: number | null
  avg_night_stay: string | null
  gross_earnings: string
  service_fees: string
  total_earnings: string
  source: string
  source_file: string
}

export interface ImportBatch {
  id: number
  source: string
  listing: number | null
  filename: string
  report_file: string
  report_url: string | null
  period_start: string | null
  period_end: string | null
  summary: Record<string, unknown>
  rows_total: number
  rows_created: number
  rows_updated: number
  rows_skipped: number
  rows_failed: number
  log: string
  created_at: string
}

export interface BillExtraction {
  amount?: string | null
  gst_amount?: string | null
  bill_date?: string | null
  period_start?: string | null
  period_end?: string | null
  supplier?: string | null
  utility_hint?: string | null
  currency?: string
  extracted_by?: string
  needs_ocr?: boolean
  message?: string
  detail?: string
  available?: boolean
}

export interface CoverageGap {
  start: string
  end: string
  days: number
}

export interface CoverageSegment {
  kind: 'covered' | 'gap'
  start: string
  end: string
  days: number
  width_pct: number
}

export interface Coverage {
  utility_type: number
  name: string
  property: number
  property_name: string
  frequency: string
  frequency_label: string
  bills: number
  bills_missing_period: number
  overlaps: number
  attachment_count: number
  computable: boolean
  reason: string | null
  start: string | null
  end: string | null
  total_days: number
  covered_days: number
  gap_days: number
  coverage_pct: number
  gaps: CoverageGap[]
  segments: CoverageSegment[]
}

// ---- Helpers ----
/** DRF list endpoints are paginated; accept either shape. */
export function listOf<T>(data: unknown): T[] {
  if (Array.isArray(data)) return data as T[]
  const maybe = data as { results?: T[] } | null
  return maybe?.results ?? []
}

export async function fetchList<T>(path: string): Promise<T[]> {
  return listOf<T>(await api(path))
}

export const money = (value: string | number | null | undefined): string => {
  if (value === null || value === undefined || value === '') return '—'
  const n = typeof value === 'number' ? value : Number(value)
  if (Number.isNaN(n)) return String(value)
  return n.toLocaleString('en-AU', { style: 'currency', currency: 'AUD' })
}

export const pct = (value: string | number | null | undefined): string => {
  if (value === null || value === undefined || value === '') return '—'
  const n = typeof value === 'number' ? value : Number(value)
  if (Number.isNaN(n)) return String(value)
  return `${(n * 100).toFixed(2)}%`
}
