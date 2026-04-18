import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'
import fs from 'fs'
import type { Plugin } from 'vite'

function localImagesPlugin(): Plugin {
  const dataDir = path.resolve(__dirname, '../data/seoul')
  return {
    name: 'local-images',
    configureServer(server) {
      server.middlewares.use('/images', (req, res, next) => {
        const filePath = path.join(dataDir, decodeURIComponent(req.url ?? ''))
        if (fs.existsSync(filePath) && fs.statSync(filePath).isFile()) {
          res.setHeader('Cache-Control', 'max-age=86400')
          fs.createReadStream(filePath).pipe(res)
        } else {
          next()
        }
      })
    },
  }
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss(), localImagesPlugin()],
  server: {
    host: true,
    allowedHosts: true,
    port: 5550,
    proxy: {
      '/api': 'http://localhost:8090',
    },
  },
})
