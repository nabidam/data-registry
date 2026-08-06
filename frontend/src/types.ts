export type Source = {
  id: number
  name: string
  kind: string
  description: string | null
  created_at: string
}

export type Batch = {
  id: number
  name: string
  source_id: number | null
  status: string
  format: string | null
  sample_count: number
  parquet_uri: string | null
  notes: string | null
  stats: Record<string, unknown> | null
  created_at: string
}

export type ImportProgress = {
  phase?: string
  message?: string
  rows_processed?: number
  shards_processed?: number
  shards_total?: number
  stage?: string
  items_processed?: number
  items_total?: number
  stage_elapsed_seconds?: number
  updated_at?: string
}

export type ImportStats = Record<string, unknown> & {
  attempt?: number
  heartbeat_at?: string
  finished_at?: string
  error?: string
  progress?: ImportProgress
}

/** Exactly one allocation per sample; reserved samples never become trainable again. */
export type Allocation = 'TRAINABLE' | 'RESERVED_EVALUATION' | 'QUARANTINED' | 'IGNORED'

export type Sample = {
  id: number
  batch_id: number
  source_id: number | null
  src_lang: string
  tgt_lang: string
  domain: string | null
  quality: number | null
  allocation: Allocation
  reserved_at: string | null
  quarantined_at: string | null
  created_at: string
  source_text?: string
  target_text?: string
}

export type Contamination = {
  id: number
  snapshot_id: number
  sample_count: number
  sample_ids: number[] | null
  reason: string | null
  created_at: string
}

export type ReservationDefaults = {
  evaluation_percent: number
  evaluation_max_samples: number
  evaluation_selector: string
  random_seed: number
  selectors: string[]
  policies: {
    contamination_safe: ContaminationSafeReservationConfig
  }
}

export type ContaminationSafeReservationConfig = {
  description: string
  selection: Record<string, unknown>
  features: Record<string, unknown>
  diversity: Record<string, unknown>
  contamination: Record<string, unknown> & {
    document_level_holdout: boolean
    near_dup_check_enabled: boolean
    near_dup_cosine_threshold: number
  }
}

export type Row = Record<string, unknown>

export type Filters = {
  src_langs?: string[]
  tgt_langs?: string[]
  domains?: string[]
  source_ids?: number[]
  batch_ids?: number[]
  min_quality?: number | null
  max_quality?: number | null
  text_contains?: string | null
}

export type Dataset = {
  id: number
  name: string
  description: string | null
  batch_ids: number[]
  filters: Filters
  created_at: string
}

export type Split = {
  id: number
  dataset_id: number
  name: string
  version: number
  seed: number
  ratios: Record<string, number>
  created_at: string
}

export type Snapshot = {
  id: number
  name: string
  dataset_id: number
  split_id: number | null
  status: string
  seed: number
  prefix_uri: string | null
  manifest: Record<string, never> | null
  stats: { counts?: Record<string, number>; total?: number } | null
  error: string | null
  created_at: string
}

export type EvaluationSet = {
  id: number
  name: string
  description: string | null
  kind: string
  parquet_uri: string | null
  sample_count: number
  created_at: string
}

export type Annotation = {
  id: number
  sample_id: number
  ignored: boolean
  quality: number | null
  review_status: string | null
  tags: string[] | null
  comment: string | null
  author: string | null
  created_at: string
}

export type Experiment = {
  id: number
  name: string
  snapshot_id: number | null
  split_id: number | null
  mlflow_run_id: string | null
  status: string
  notes: string | null
  created_at: string
}

export type Model = {
  id: number
  name: string
  experiment_id: number | null
  checkpoint_uri: string | null
  metrics: Record<string, number> | null
  notes: string | null
  created_at: string
}

export type Overview = {
  samples: number
  batches: number
  sources: number
  datasets: number
  snapshots: number
  evaluation_sets: number
  experiments: number
  models: number
  trainable_samples: number
  reserved_samples: number
  quarantined_samples: number
  ignored_samples: number
  contaminated_snapshots: number
}
