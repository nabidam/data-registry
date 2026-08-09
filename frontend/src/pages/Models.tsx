import { useState } from 'react'
import { HStack, VStack } from '@astryxdesign/core/Stack'
import { Grid } from '@astryxdesign/core/Grid'

import { DataTable } from '@/components/DataTable'
import { EditEntity } from '@/components/EditEntity'
import { Button, Card, ErrorBox, Field, Input, PageHeader, Select } from '@/components/ui'
import { useCreate, useList, usePatch, useRemove } from '@/hooks/useResource'
import type { Experiment, Model, Row } from '@/types'

export default function Models() {
  const models = useList<Model>('models')
  const experiments = useList<Experiment>('experiments', { limit: 200 })
  const create = useCreate<Model>('models')
  const remove = useRemove('models')
  const patch = usePatch<Model>('models')
  const [editing, setEditing] = useState<Model | null>(null)
  const [form, setForm] = useState({
    name: '',
    experiment_id: '',
    checkpoint_uri: '',
    metrics: '',
    notes: '',
  })

  return (
    <VStack gap={4}>
      <PageHeader title="Models" subtitle="Checkpoints and their evaluation metrics" />
      <ErrorBox error={create.error} />

      <Card>
        <form
          onSubmit={(e) => {
            e.preventDefault()
            let metrics: Record<string, number> | null = null
            if (form.metrics.trim()) {
              try {
                metrics = JSON.parse(form.metrics)
              } catch {
                metrics = null
              }
            }
            create.mutate({
              name: form.name,
              experiment_id: form.experiment_id ? Number(form.experiment_id) : null,
              checkpoint_uri: form.checkpoint_uri || null,
              metrics,
              notes: form.notes || null,
            })
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
            <Field label="Experiment">
              <Select
                value={form.experiment_id}
                onChange={(e) => setForm({ ...form, experiment_id: e.target.value })}
              >
                <option value="">—</option>
                {experiments.data?.map((x) => (
                  <option key={x.id} value={x.id}>
                    {x.name}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Checkpoint URI">
              <Input
                value={form.checkpoint_uri}
                onChange={(e) => setForm({ ...form, checkpoint_uri: e.target.value })}
              />
            </Field>
            <Field label='Metrics JSON ({"bleu": 32.1})'>
              <Input
                value={form.metrics}
                onChange={(e) => setForm({ ...form, metrics: e.target.value })}
              />
            </Field>
            <Field label="Notes">
              <Input
                value={form.notes}
                onChange={(e) => setForm({ ...form, notes: e.target.value })}
              />
            </Field>
            <HStack vAlign="end">
              <Button disabled={create.isPending}>Add model</Button>
            </HStack>
          </Grid>
        </form>
      </Card>

      {editing && (
        <EditEntity
          title={`Edit model ${editing.name}`}
          fields={[
            { key: 'name', label: 'Name', required: true },
            { key: 'checkpoint_uri', label: 'Checkpoint URI' },
            { key: 'metrics', label: 'Metrics JSON ({"bleu": 32.1})', type: 'textarea' },
            { key: 'notes', label: 'Notes', type: 'textarea' },
          ]}
          initial={{
            name: editing.name,
            checkpoint_uri: editing.checkpoint_uri ?? '',
            metrics: editing.metrics ? JSON.stringify(editing.metrics) : '',
            notes: editing.notes ?? '',
          }}
          error={patch.error}
          isSaving={patch.isPending}
          onClose={() => setEditing(null)}
          onSave={(values) => {
            let metrics: Record<string, number> | null = null
            if (values.metrics.trim()) {
              try {
                metrics = JSON.parse(values.metrics)
              } catch {
                metrics = null
              }
            }
            patch.mutate(
              {
                id: editing.id,
                body: {
                  name: values.name,
                  checkpoint_uri: values.checkpoint_uri || null,
                  metrics,
                  notes: values.notes || null,
                },
              },
              { onSuccess: () => setEditing(null) },
            )
          }}
        />
      )}

      <DataTable
        loading={models.isLoading}
        rows={models.data as unknown as Row[]}
        columns={[
          { key: 'id', header: 'ID', width: '60px' },
          { key: 'name', header: 'Name' },
          { key: 'experiment_id', header: 'Experiment' },
          { key: 'checkpoint_uri', header: 'Checkpoint' },
          { key: 'metrics', header: 'Metrics', render: (r) => JSON.stringify(r.metrics ?? {}) },
          {
            key: 'actions',
            header: '',
            render: (r) => (
              <HStack gap={2}>
                <Button variant="ghost" onClick={() => setEditing(r as unknown as Model)}>
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
