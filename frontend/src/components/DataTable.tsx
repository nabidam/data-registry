import type { ReactNode } from 'react'
import { Table, type TableColumn } from '@astryxdesign/core/Table'
import { EmptyState } from '@astryxdesign/core/EmptyState'
import { VStack } from '@astryxdesign/core/Stack'

export type Column<T> = {
  key: string
  header: string
  render?: (row: T) => ReactNode
  width?: string
}

export function DataTable<T extends Record<string, unknown>>({
  columns,
  rows,
  loading,
  empty = 'Nothing here yet.',
}: {
  columns: Column<T>[]
  rows: T[] | undefined
  loading?: boolean
  empty?: string
}) {
  if (loading) {
    return (
      <VStack gap={3} width="100%">
        <div className="w-full h-9 bg-slate-800/60 rounded animate-pulse" />
        <div className="w-full h-11 bg-slate-800/30 rounded animate-pulse" />
        <div className="w-full h-11 bg-slate-800/20 rounded animate-pulse" />
        <div className="w-full h-11 bg-slate-800/20 rounded animate-pulse" />
      </VStack>
    )
  }

  if (!rows?.length) {
    return (
      <EmptyState
        title="No items"
        description={empty}
      />
    )
  }

  const normalizedRows = rows.map((row, index) => {
    const compositeKey = row.id ?? row.sample_id ?? row.snapshot_id ?? row.source_id ?? (row.src_lang && row.tgt_lang ? `${row.src_lang}_${row.tgt_lang}` : index)
    return {
      id: compositeKey,
      ...row,
    }
  })

  const tableColumns: TableColumn<(typeof normalizedRows)[number]>[] = columns.map((col) => ({
    key: col.key,
    header: col.header,
    width: col.width ? (col.width as any) : undefined,
    renderCell: col.render
      ? (row) => col.render!(row as T)
      : (row) => {
          const val = (row as Record<string, unknown>)[col.key]
          if (typeof val === 'number') {
            return val.toLocaleString()
          }
          return String(val ?? '')
        },
  }))

  return (
    <Table<(typeof normalizedRows)[number]>
      data={normalizedRows}
      columns={tableColumns}
      hasHover
      idKey="id"
    />
  )
}
