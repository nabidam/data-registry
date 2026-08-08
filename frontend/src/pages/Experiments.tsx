import { useState } from 'react'

import { DataTable } from '@/components/DataTable'
import { EditEntity } from '@/components/EditEntity'
import { Button, Card, ErrorBox, Field, Input, PageHeader, Select } from '@/components/ui'
import { useCreate, useList, usePatch, useRemove } from '@/hooks/useResource'
import type { Experiment, Row, Snapshot, Split } from '@/types'

const STATUSES = ['planned', 'running', 'done', 'failed']

export default function Experiments() {
  const experiments = useList<Experiment>('experiments')
  const snapshots = useList<Snapshot>('snapshots', { limit: 200 })
  const splits = useList<Split>('splits', { limit: 200 })
  const create = useCreate<Experiment>('experiments')
  const remove = useRemove('experiments')
  const patch = usePatch<Experiment>('experiments')
  const [editing, setEditing] = useState<Experiment | null>(null)
  const [form, setForm] = useState({
    name: '',
    snapshot_id: '',
    split_id: '',
    mlflow_run_id: '',
    status: 'planned',
    notes: '',
  })

  return (
    <>
      <PageHeader title="Experiments" subtitle="Training runs. Only references are stored." />
      <ErrorBox error={create.error} />

      <Card className="mb-4">
        <form
          className="grid gap-3 md:grid-cols-4"
          onSubmit={(e) => {
            e.preventDefault()
            create.mutate({
              name: form.name,
              snapshot_id: form.snapshot_id ? Number(form.snapshot_id) : null,
              split_id: form.split_id ? Number(form.split_id) : null,
              mlflow_run_id: form.mlflow_run_id || null,
              status: form.status,
              notes: form.notes || null,
            })
          }}
        >
          <Field label="Name">
            <Input
              required
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
            />
          </Field>
          <Field label="Snapshot">
            <Select
              value={form.snapshot_id}
              onChange={(e) => setForm({ ...form, snapshot_id: e.target.value })}
            >
              <option value="">—</option>
              {snapshots.data?.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Split">
            <Select
              value={form.split_id}
              onChange={(e) => setForm({ ...form, split_id: e.target.value })}
            >
              <option value="">—</option>
              {splits.data?.map((s) => (
                <option key={s.id} value={s.id}>
                  #{s.id} {s.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="MLflow run id">
            <Input
              value={form.mlflow_run_id}
              onChange={(e) => setForm({ ...form, mlflow_run_id: e.target.value })}
            />
          </Field>
          <Field label="Status">
            <Select
              value={form.status}
              onChange={(e) => setForm({ ...form, status: e.target.value })}
            >
              {STATUSES.map((s) => (
                <option key={s}>{s}</option>
              ))}
            </Select>
          </Field>
          <Field label="Notes">
            <Input
              value={form.notes}
              onChange={(e) => setForm({ ...form, notes: e.target.value })}
            />
          </Field>
          <div className="flex items-end">
            <Button disabled={create.isPending}>Add experiment</Button>
          </div>
        </form>
      </Card>

      {editing && (
        <EditEntity
          title={`Edit experiment ${editing.name}`}
          fields={[
            { key: 'name', label: 'Name', required: true },
            { key: 'mlflow_run_id', label: 'MLflow run id' },
            { key: 'status', label: 'Status', type: 'select', options: STATUSES },
            { key: 'notes', label: 'Notes', type: 'textarea' },
          ]}
          initial={editing as unknown as Record<string, unknown>}
          error={patch.error}
          isSaving={patch.isPending}
          onClose={() => setEditing(null)}
          onSave={(values) =>
            patch.mutate(
              {
                id: editing.id,
                body: {
                  name: values.name,
                  mlflow_run_id: values.mlflow_run_id || null,
                  status: values.status,
                  notes: values.notes || null,
                },
              },
              { onSuccess: () => setEditing(null) },
            )
          }
        />
      )}

      <DataTable
        loading={experiments.isLoading}
        rows={experiments.data as unknown as Row[]}
        columns={[
          { key: 'id', header: 'ID', width: '60px' },
          { key: 'name', header: 'Name' },
          { key: 'snapshot_id', header: 'Snapshot' },
          { key: 'split_id', header: 'Split' },
          { key: 'mlflow_run_id', header: 'MLflow run' },
          { key: 'status', header: 'Status' },
          {
            key: 'actions',
            header: '',
            render: (r) => (
              <div className="flex gap-2">
                <Button variant="ghost" onClick={() => setEditing(r as unknown as Experiment)}>
                  Edit
                </Button>
                <Button variant="danger" onClick={() => remove.mutate(r.id as number)}>
                  Delete
                </Button>
              </div>
            ),
          },
        ]}
      />
    </>
  )
}
