import { useState } from 'react'
import { HStack, VStack } from '@astryxdesign/core/Stack'
import { Grid } from '@astryxdesign/core/Grid'

import { DataTable } from '@/components/DataTable'
import { EditEntity } from '@/components/EditEntity'
import { Button, Card, ErrorBox, Field, Input, PageHeader, Select } from '@/components/ui'
import { useCreate, useList, usePatch, useRemove } from '@/hooks/useResource'
import type { Source } from '@/types'

const KINDS = ['corpus', 'crawl', 'human', 'synthetic', 'other']

export default function Sources() {
  const { data, isLoading } = useList<Source>('sources')
  const create = useCreate<Source>('sources')
  const remove = useRemove('sources')
  const patch = usePatch<Source>('sources')
  const [form, setForm] = useState({ name: '', kind: 'corpus', description: '' })
  const [editing, setEditing] = useState<Source | null>(null)

  return (
    <VStack gap={4}>
      <PageHeader title="Sources" subtitle="Where the data originated" />
      <ErrorBox error={create.error} />

      <Card>
        <form
          onSubmit={(e) => {
            e.preventDefault()
            create.mutate(form, { onSuccess: () => setForm({ ...form, name: '', description: '' }) })
          }}
        >
          <Grid columns={4} gap={3} align="end">
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
            <HStack vAlign="end">
              <Button disabled={create.isPending}>Add source</Button>
            </HStack>
          </Grid>
        </form>
      </Card>

      {editing && (
        <EditEntity
          title={`Edit source ${editing.name}`}
          fields={[
            { key: 'name', label: 'Name', required: true },
            { key: 'kind', label: 'Kind', type: 'select', options: KINDS },
            { key: 'description', label: 'Description', type: 'textarea' },
          ]}
          initial={editing as unknown as Record<string, unknown>}
          error={patch.error}
          isSaving={patch.isPending}
          onClose={() => setEditing(null)}
          onSave={(values) =>
            patch.mutate(
              {
                id: editing.id,
                body: { name: values.name, kind: values.kind, description: values.description },
              },
              { onSuccess: () => setEditing(null) },
            )
          }
        />
      )}

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
              <HStack gap={2}>
                <Button variant="ghost" onClick={() => setEditing(r as unknown as Source)}>
                  Edit
                </Button>
                <Button variant="danger" onClick={() => remove.mutate(r.id as number)}>
                  Delete
                </Button>
              </HStack>
            ),
          },
        ]}
      />
    </VStack>
  )
}
