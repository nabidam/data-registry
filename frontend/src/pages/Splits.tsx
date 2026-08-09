import { useState } from 'react'
import { HStack, VStack } from '@astryxdesign/core/Stack'
import { Grid } from '@astryxdesign/core/Grid'

import { DataTable } from '@/components/DataTable'
import { EditEntity } from '@/components/EditEntity'
import { Button, Card, ErrorBox, Field, Input, PageHeader, Select } from '@/components/ui'
import { useCreate, useList, usePatch } from '@/hooks/useResource'
import type { Dataset, Row, Split } from '@/types'

export default function Splits() {
  const splits = useList<Split>('splits')
  const datasets = useList<Dataset>('datasets', { limit: 200 })
  const create = useCreate<Split>('splits')
  const patch = usePatch<Split>('splits')
  const [editing, setEditing] = useState<Split | null>(null)
  const [form, setForm] = useState({
    dataset_id: '',
    name: 'default',
    version: '1',
    seed: '42',
    train: '0.9',
    validation: '0.05',
    test: '0.05',
  })

  return (
    <VStack gap={4}>
      <PageHeader
        title="Splits"
        subtitle="Reproducible train/validation/test recipes. Existing splits are never modified."
      />
      <ErrorBox error={create.error} />

      <Card>
        <form
          onSubmit={(e) => {
            e.preventDefault()
            create.mutate({
              dataset_id: Number(form.dataset_id),
              name: form.name,
              version: Number(form.version),
              seed: Number(form.seed),
              ratios: {
                train: Number(form.train),
                validation: Number(form.validation),
                test: Number(form.test),
              },
            })
          }}
        >
          <Grid columns={4} gap={3} align="end">
            <Field label="Dataset">
              <Select
                required
                value={form.dataset_id}
                onChange={(e) => setForm({ ...form, dataset_id: e.target.value })}
              >
                <option value="">select…</option>
                {datasets.data?.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Name">
              <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </Field>
            <Field label="Version">
              <Input
                type="number"
                value={form.version}
                onChange={(e) => setForm({ ...form, version: e.target.value })}
              />
            </Field>
            <Field label="Seed">
              <Input
                type="number"
                value={form.seed}
                onChange={(e) => setForm({ ...form, seed: e.target.value })}
              />
            </Field>
            <Field label="Train">
              <Input
                type="number"
                step="0.01"
                value={form.train}
                onChange={(e) => setForm({ ...form, train: e.target.value })}
              />
            </Field>
            <Field label="Validation">
              <Input
                type="number"
                step="0.01"
                value={form.validation}
                onChange={(e) => setForm({ ...form, validation: e.target.value })}
              />
            </Field>
            <Field label="Test">
              <Input
                type="number"
                step="0.01"
                value={form.test}
                onChange={(e) => setForm({ ...form, test: e.target.value })}
              />
            </Field>
            <HStack vAlign="end">
              <Button disabled={create.isPending}>Create split</Button>
            </HStack>
          </Grid>
        </form>
      </Card>

      {editing && (
        <EditEntity
          title={`Edit split ${editing.name}`}
          fields={[{ key: 'name', label: 'Name', required: true }]}
          initial={editing as unknown as Record<string, unknown>}
          error={patch.error}
          isSaving={patch.isPending}
          onClose={() => setEditing(null)}
          onSave={(values) =>
            patch.mutate(
              { id: editing.id, body: { name: values.name } },
              { onSuccess: () => setEditing(null) },
            )
          }
        />
      )}

      <DataTable
        loading={splits.isLoading}
        rows={splits.data as unknown as Row[]}
        columns={[
          { key: 'id', header: 'ID', width: '60px' },
          { key: 'dataset_id', header: 'Dataset' },
          { key: 'name', header: 'Name' },
          { key: 'version', header: 'Version' },
          { key: 'seed', header: 'Seed' },
          { key: 'ratios', header: 'Ratios', render: (r) => JSON.stringify(r.ratios) },
          {
            key: 'actions',
            header: '',
            render: (r) => (
              <Button variant="ghost" onClick={() => setEditing(r as unknown as Split)}>
                Edit
              </Button>
            ),
          },
        ]}
      />
    </VStack>
  )
}
