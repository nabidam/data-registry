import { useState } from 'react'

import { DataTable } from '@/components/DataTable'
import { Button, Card, ErrorBox, Field, Input, PageHeader, Select } from '@/components/ui'
import { useCreate, useList, useRemove } from '@/hooks/useResource'
import type { Annotation, Row } from '@/types'

export default function Annotations() {
  const annotations = useList<Annotation>('annotations')
  const create = useCreate<Annotation>('annotations')
  const remove = useRemove('annotations')
  const [form, setForm] = useState({
    sample_id: '',
    ignored: 'false',
    quality: '',
    review_status: '',
    tags: '',
    comment: '',
    author: '',
  })

  return (
    <>
      <PageHeader
        title="Annotations"
        subtitle="Per-sample metadata. Original data is never modified; ignored samples are excluded at build time."
      />
      <ErrorBox error={create.error} />

      <Card className="mb-4">
        <form
          className="grid gap-3 md:grid-cols-4"
          onSubmit={(e) => {
            e.preventDefault()
            create.mutate({
              sample_id: Number(form.sample_id),
              ignored: form.ignored === 'true',
              quality: form.quality ? Number(form.quality) : null,
              review_status: form.review_status || null,
              tags: form.tags ? form.tags.split(',').map((t) => t.trim()) : null,
              comment: form.comment || null,
              author: form.author || null,
            })
          }}
        >
          <Field label="Sample id">
            <Input
              required
              type="number"
              value={form.sample_id}
              onChange={(e) => setForm({ ...form, sample_id: e.target.value })}
            />
          </Field>
          <Field label="Ignored">
            <Select
              value={form.ignored}
              onChange={(e) => setForm({ ...form, ignored: e.target.value })}
            >
              <option value="false">no</option>
              <option value="true">yes</option>
            </Select>
          </Field>
          <Field label="Quality">
            <Input
              type="number"
              step="0.01"
              value={form.quality}
              onChange={(e) => setForm({ ...form, quality: e.target.value })}
            />
          </Field>
          <Field label="Review status">
            <Select
              value={form.review_status}
              onChange={(e) => setForm({ ...form, review_status: e.target.value })}
            >
              <option value="">—</option>
              <option value="pending">pending</option>
              <option value="approved">approved</option>
              <option value="rejected">rejected</option>
            </Select>
          </Field>
          <Field label="Tags">
            <Input
              placeholder="noisy,ocr"
              value={form.tags}
              onChange={(e) => setForm({ ...form, tags: e.target.value })}
            />
          </Field>
          <Field label="Comment">
            <Input
              value={form.comment}
              onChange={(e) => setForm({ ...form, comment: e.target.value })}
            />
          </Field>
          <Field label="Author">
            <Input
              value={form.author}
              onChange={(e) => setForm({ ...form, author: e.target.value })}
            />
          </Field>
          <div className="flex items-end">
            <Button disabled={create.isPending}>Add annotation</Button>
          </div>
        </form>
      </Card>

      <DataTable
        loading={annotations.isLoading}
        rows={annotations.data as unknown as Row[]}
        columns={[
          { key: 'id', header: 'ID', width: '60px' },
          { key: 'sample_id', header: 'Sample' },
          { key: 'ignored', header: 'Ignored', render: (r) => (r.ignored ? 'yes' : 'no') },
          { key: 'quality', header: 'Quality' },
          { key: 'review_status', header: 'Review' },
          { key: 'tags', header: 'Tags', render: (r) => ((r.tags as string[]) ?? []).join(', ') },
          { key: 'comment', header: 'Comment' },
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
