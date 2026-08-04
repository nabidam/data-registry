import { Route, Routes } from 'react-router-dom'

import Layout from '@/components/Layout'
import Annotations from '@/pages/Annotations'
import Batches from '@/pages/Batches'
import Dashboard from '@/pages/Dashboard'
import Datasets from '@/pages/Datasets'
import EvaluationSets from '@/pages/EvaluationSets'
import Experiments from '@/pages/Experiments'
import Exports from '@/pages/Exports'
import Imports from '@/pages/Imports'
import Models from '@/pages/Models'
import Samples from '@/pages/Samples'
import Settings from '@/pages/Settings'
import Snapshots from '@/pages/Snapshots'
import Splits from '@/pages/Splits'
import Sources from '@/pages/Sources'
import Statistics from '@/pages/Statistics'

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<Dashboard />} />
        <Route path="/samples" element={<Samples />} />
        <Route path="/batches" element={<Batches />} />
        <Route path="/sources" element={<Sources />} />
        <Route path="/datasets" element={<Datasets />} />
        <Route path="/snapshots" element={<Snapshots />} />
        <Route path="/splits" element={<Splits />} />
        <Route path="/evaluation-sets" element={<EvaluationSets />} />
        <Route path="/annotations" element={<Annotations />} />
        <Route path="/experiments" element={<Experiments />} />
        <Route path="/models" element={<Models />} />
        <Route path="/statistics" element={<Statistics />} />
        <Route path="/imports" element={<Imports />} />
        <Route path="/exports" element={<Exports />} />
        <Route path="/settings" element={<Settings />} />
      </Route>
    </Routes>
  )
}
