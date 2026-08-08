import { useState } from 'react'

import { Button, Card, ErrorBox, Field, Input, Select, Textarea } from '@/components/ui'

export type EditField = {
  key: string
  label: string
  type?: 'text' | 'select' | 'textarea'
  options?: string[]
  required?: boolean
}

/** Inline edit form for a single entity. Fields are strings only; callers
 *  translate them into the right payload shape before patching. */
export function EditEntity({
  fields,
  initial,
  title,
  error,
  isSaving,
  onSave,
  onClose,
}: {
  fields: EditField[]
  initial: Record<string, unknown>
  title: string
  error?: unknown
  isSaving?: boolean
  onSave: (values: Record<string, string>) => void
  onClose: () => void
}) {
  const [values, setValues] = useState<Record<string, string>>(() => {
    const out: Record<string, string> = {}
    for (const field of fields) out[field.key] = String(initial[field.key] ?? '')
    return out
  })

  const set = (key: string, value: string) => setValues((current) => ({ ...current, [key]: value }))

  return (
    <Card className="mb-4">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="font-medium">{title}</h2>
        <Button variant="ghost" onClick={onClose}>
          Cancel
        </Button>
      </div>
      <ErrorBox error={error} />
      <form
        className="grid gap-3 md:grid-cols-4"
        onSubmit={(e) => {
          e.preventDefault()
          onSave(values)
        }}
      >
        {fields.map((field) => (
          <Field key={field.key} label={field.label}>
            {field.type === 'select' ? (
              <Select
                required={field.required}
                value={values[field.key]}
                onChange={(e) => set(field.key, e.target.value)}
              >
                {field.options?.map((option) => (
                  <option key={option}>{option}</option>
                ))}
              </Select>
            ) : field.type === 'textarea' ? (
              <Textarea
                rows={3}
                value={values[field.key]}
                onChange={(e) => set(field.key, e.target.value)}
              />
            ) : (
              <Input
                required={field.required}
                value={values[field.key]}
                onChange={(e) => set(field.key, e.target.value)}
              />
            )}
          </Field>
        ))}
        <div className="flex items-end">
          <Button disabled={isSaving}>Save</Button>
        </div>
      </form>
    </Card>
  )
}