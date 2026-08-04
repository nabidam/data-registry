import type { ReactNode } from 'react'

import { Empty } from '@/components/ui'

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
  if (loading) return <Empty message="Loading…" />
  if (!rows?.length) return <Empty message={empty} />

  return (
    <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
      <table className="w-full text-sm">
        <thead className="bg-slate-100 text-left text-xs uppercase tracking-wide text-slate-500">
          <tr>
            {columns.map((c) => (
              <th key={c.key} className="px-3 py-2 font-medium" style={{ width: c.width }}>
                {c.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className="border-t border-slate-100 hover:bg-slate-50">
              {columns.map((c) => (
                <td key={c.key} className="max-w-md truncate px-3 py-2 align-top">
                  {c.render ? c.render(row) : String(row[c.key] ?? '')}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
