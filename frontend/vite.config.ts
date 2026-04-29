import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'
import fs from 'fs'
import { execSync } from 'child_process'
import type { Plugin } from 'vite'

function getGitSHA() {
  try { return execSync('git rev-parse --short HEAD', { cwd: path.resolve(__dirname, '..') }).toString().trim() }
  catch { return 'dev' }
}

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
  define: {
    __GIT_SHA__: JSON.stringify(getGitSHA()),
    __BUILD_DATE__: JSON.stringify(new Date().toISOString()),
  },
  plugins: [react(), tailwindcss(), localImagesPlugin()],
  server: {
    host: true,
    allowedHosts: true,
    port: 5550,
    cors: true,
    proxy: {
      '/api': 'http://127.0.0.1:13854',
    },
    hmr: {
      protocol: 'wss',
      clientPort: 443,
    },
  },
})
