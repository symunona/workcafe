import { useMemo } from 'react'
import { 
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  PieChart, Pie, Cell
} from 'recharts'
import type { Cafe } from '../types'
import { getImages, PROVIDER_COLORS } from '../utils'
import { CloseIcon } from './Icons'

interface StatsModalProps {
  cafes: Cafe[]
  onClose: () => void
}

export function StatsModal({ cafes, onClose }: StatsModalProps) {
  const stats = useMemo(() => {
    const providerStats: Record<string, { count: number; totalImages: number }> = {}

    cafes.forEach(cafe => {
      const p = cafe.provider
      if (!providerStats[p]) {
        providerStats[p] = { count: 0, totalImages: 0 }
      }
      providerStats[p].count += 1
      providerStats[p].totalImages += getImages(cafe).length
    })

    const data = Object.keys(providerStats).map(p => ({
      provider: p,
      cafes: providerStats[p].count,
      totalImages: providerStats[p].totalImages,
      avgImages: providerStats[p].count > 0 
        ? Number((providerStats[p].totalImages / providerStats[p].count).toFixed(2)) 
        : 0,
      fill: PROVIDER_COLORS[p] || '#6b7280'
    }))

    return data.sort((a, b) => b.cafes - a.cafes)
  }, [cafes])

  return (
    <div className="fixed inset-0 z-[2000] bg-black/60 flex items-center justify-center p-4 sm:p-6 backdrop-blur-sm animate-in fade-in">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-h-full max-w-4xl overflow-y-auto flex flex-col relative">
        <div className="sticky top-0 bg-white/90 backdrop-blur-md px-4 sm:px-6 py-4 border-b border-gray-100 flex items-center justify-between z-10">
          <h2 className="text-lg sm:text-xl font-bold text-gray-900">Database Statistics</h2>
          <button 
            onClick={onClose}
            className="w-10 h-10 rounded-full bg-gray-100 flex items-center justify-center text-gray-500 hover:bg-gray-200 transition-colors"
          >
            <CloseIcon />
          </button>
        </div>

        <div className="p-4 sm:p-6 grid grid-cols-1 md:grid-cols-2 gap-6 sm:gap-8">
          {/* Cafe Count by Provider */}
          <div className="bg-gray-50 rounded-xl p-4">
            <h3 className="text-xs sm:text-sm font-semibold text-gray-500 uppercase tracking-wider mb-4">Total Cafes by Provider</h3>
            <div className="h-56 sm:h-64">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie
                    data={stats}
                    dataKey="cafes"
                    nameKey="provider"
                    cx="50%"
                    cy="50%"
                    outerRadius={window.innerWidth < 640 ? 60 : 80}
                    label={({ name, percent }) => `${name} ${((percent || 0) * 100).toFixed(0)}%`}
                    labelLine={window.innerWidth >= 640}
                  >
                    {stats.map((entry, index) => (
                      <Cell key={`cell-${index}`} fill={entry.fill} />
                    ))}
                  </Pie>
                  <Tooltip formatter={(value) => [value, 'Cafes']} />
                </PieChart>
              </ResponsiveContainer>
            </div>
          </div>

          {/* Avg Images by Provider */}
          <div className="bg-gray-50 rounded-xl p-4">
            <h3 className="text-xs sm:text-sm font-semibold text-gray-500 uppercase tracking-wider mb-4">Average Images per Cafe</h3>
            <div className="h-56 sm:h-64">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={stats} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#e5e7eb" />
                  <XAxis dataKey="provider" axisLine={false} tickLine={false} tick={{ fontSize: window.innerWidth < 640 ? 10 : 12 }} />
                  <YAxis axisLine={false} tickLine={false} tick={{ fontSize: window.innerWidth < 640 ? 10 : 12 }} />
                  <Tooltip cursor={{ fill: '#f3f4f6' }} />
                  <Bar dataKey="avgImages" name="Avg Images" radius={[4, 4, 0, 0]}>
                    {stats.map((entry, index) => (
                      <Cell key={`cell-${index}`} fill={entry.fill} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>

          {/* Total Images by Provider */}
          <div className="bg-gray-50 rounded-xl p-4 md:col-span-2">
            <h3 className="text-xs sm:text-sm font-semibold text-gray-500 uppercase tracking-wider mb-4">Total Images Collected</h3>
            <div className="h-64 sm:h-72">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={stats} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#e5e7eb" />
                  <XAxis dataKey="provider" axisLine={false} tickLine={false} tick={{ fontSize: window.innerWidth < 640 ? 10 : 12 }} />
                  <YAxis axisLine={false} tickLine={false} tick={{ fontSize: window.innerWidth < 640 ? 10 : 12 }} />
                  <Tooltip cursor={{ fill: '#f3f4f6' }} />
                  <Bar dataKey="totalImages" name="Total Images" radius={[4, 4, 0, 0]}>
                    {stats.map((entry, index) => (
                      <Cell key={`cell-${index}`} fill={entry.fill} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}