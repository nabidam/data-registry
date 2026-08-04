import { useState } from 'react'

import { DataTable } from '@/components/DataTable'
import { Button, Card, ErrorBox, Field, Input, PageHeader, Select } from '@/components/ui'
import { useCreate, useList, useRemove } from '@/hooks/useResource'
import type { Source } from '@/types'

const KINDS = ['corpus', 'crawl', 'human', 'synthetic', 'other']

export default function Sources() {
  const { data, isLoading } = useList<Source>('sources')
  const create = useCreate<Source>('sources')
  const remove = useRemove('sources')
  const [form, setForm] = useState({ name: '', kind: 'corpus', description: '' })

  return (
    <>
      <PageHeader title="Sources" subtitle="Where the data originated" />
      <ErrorBox error={create.error} />

      <Card className="mb-4">
        <form
          className="grid gap-3 md:grid-cols-4"
          onSubmit={(e) => {
            e.preventDefault()
            create.mutate(form, { onSuccess: () => setForm({ ...form, name: '', description: '' }) })
          }}
        >
          <Field label="Name">
            <Input
              required
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
            />
          </Field>
          <Field label="Kind">
            <Select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>
              {KINDS.map((k) => (
                <option key={k}>{k}</option>
              ))}
            </Select>
          </Field>
          <Field label="Description">
            <Input
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
            />
          </Field>
          <div className="flex items-end">
            <Button disabled={create.isPending}>Add source</Button>
          </div>
        </form>
      </Card>

      <DataTable
        loading={isLoading}
        rows={data as unknown as Record<string, unknown>[]}
        columns={[
          { key: 'id', header: 'ID', width: '60px' },
          { key: 'name', header: 'Name' },
          { key: 'kind', header: 'Kind' },
          { key: 'description', header: 'Description' },
          {
            key: 'actions',
            header: '',
            render: (r) => (
              <Button variant="danger" onClick={() => remove.mutate(r.id as number)}>
                Delete
              </Button>
            ),
          },
        ]}
      />
    </>
  )
}
