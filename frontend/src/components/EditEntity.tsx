import { useState } from 'react'
import { Heading } from '@astryxdesign/core/Text'
import { HStack, VStack } from '@astryxdesign/core/Stack'
import { Grid } from '@astryxdesign/core/Grid'
import { Button, Card, ErrorBox, Field, Input, Select, Textarea } from '@/components/ui'

export type EditField = {
  key: string
  label: string
  type?: 'text' | 'select' | 'textarea'
  options?: string[]
  required?: boolean
}

export function EditEntity({
  title,
  fields,
  initial,
  onSave,
  onClose,
  isSaving,
  error,
}: {
  title: string
  fields: EditField[]
  initial: Record<string, unknown>
  onSave: (values: Record<string, string>) => void
  onClose: () => void
  isSaving?: boolean
  error?: unknown
}) {
  const [values, setValues] = useState<Record<string, string>>(() => {
    const init: Record<string, string> = {}
    for (const f of fields) {
      init[f.key] = String(initial[f.key] ?? '')
    }
    return init
  })

  const set = (key: string, value: string) => setValues((current: Record<string, string>) => ({ ...current, [key]: value }))

  return (
    <Card className="mb-4">
      <VStack gap={3}>
        <HStack vAlign="center" hAlign="between">
          <Heading level={4}>{title}</Heading>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
        </HStack>
        <ErrorBox error={error} />
        <form
          onSubmit={(e) => {
            e.preventDefault()
            onSave(values)
          }}
        >
          <Grid columns={4} gap={3} align="end">
            {fields.map((f) => (
              <Field key={f.key} label={f.label}>
                {f.type === 'select' ? (
                  <Select value={values[f.key]} onChange={(e) => set(f.key, e.target.value)}>
                    {(f.options ?? []).map((o) => (
                      <option key={o}>{o}</option>
                    ))}
                  </Select>
                ) : f.type === 'textarea' ? (
                  <Textarea value={values[f.key]} onChange={(e) => set(f.key, e.target.value)} />
                ) : (
                  <Input
                    required={f.required}
                    value={values[f.key]}
                    onChange={(e) => set(f.key, e.target.value)}
                  />
                )}
              </Field>
            ))}
            <HStack vAlign="end">
              <Button disabled={isSaving}>Save</Button>
            </HStack>
          </Grid>
        </form>
      </VStack>
    </Card>
  )
}