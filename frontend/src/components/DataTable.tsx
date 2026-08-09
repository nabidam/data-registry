import type { ReactNode } from 'react'
import { Table, type TableColumn } from '@astryxdesign/core/Table'
import { EmptyState } from '@astryxdesign/core/EmptyState'
import { Spinner } from '@astryxdesign/core/Spinner'
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
      <VStack gap={4} hAlign="center" vAlign="center" style={{ padding: '3rem 0' }}>
        <Spinner size="lg" />
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

  const normalizedRows = rows.map((row, index) => ({
    id: row.id ?? row.sample_id ?? row.snapshot_id ?? row.source_id ?? index,
    ...row,
  }))

  const tableColumns: TableColumn<(typeof normalizedRows)[number]>[] = columns.map((col) => ({
    key: col.key,
    header: col.header,
    width: col.width ? (col.width as any) : undefined,
    renderCell: col.render
      ? (row) => col.render!(row as T)
      : (row) => String((row as Record<string, unknown>)[col.key] ?? ''),
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
