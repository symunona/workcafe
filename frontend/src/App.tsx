import { BrowserRouter, Routes, Route } from 'react-router-dom'
import CleanApp from './CleanApp'
import ScraperApp from './ScraperApp'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/scraper/*" element={<ScraperApp />} />
        <Route path="/cafe/:id" element={<CleanApp />} />
        <Route path="/*" element={<CleanApp />} />
      </Routes>
    </BrowserRouter>
  )
}
