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

export type Sample = {
  id: number
  batch_id: number
  source_id: number | null
  src_lang: string
  tgt_lang: string
  domain: string | null
  quality: number | null
  status: string
  created_at: string
  source_text?: string
  target_text?: string
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
  include_ignored?: boolean
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
  ignored_samples: number
}
