import { useQuery } from '@tanstack/react-query'

import { Card, PageHeader } from '@/components/ui'
import { api } from '@/lib/api'

export default function Settings() {
  const { data, isLoading } = useQuery({
    queryKey: ['settings'],
    queryFn: () => api.get<Record<string, string | null>>('/settings'),
  })

  return (
    <>
      <PageHeader
        title="Settings"
        subtitle="Read-only deployment configuration (set via environment variables)"
      />
      <Card>
        {isLoading ? (
          <p className="text-sm text-slate-500">Loading…</p>
        ) : (
          <dl className="grid gap-2 text-sm md:grid-cols-2">
            {Object.entries(data ?? {}).map(([key, value]) => (
              <div key={key} className="flex justify-between gap-4 border-b border-slate-100 py-1">
                <dt className="text-slate-500">{key}</dt>
                <dd className="font-mono">{value ?? '—'}</dd>
              </div>
            ))}
          </dl>
        )}
      </Card>
    </>
  )
}
