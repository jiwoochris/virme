import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

/* @types/node 를 새로 넣지 않으려고 필요한 만큼만 선언한다. 설정 파일은 Node 에서 실행된다. */
declare const process: { env: Record<string, string | undefined> }

/* 모델·core 서버 주소. 기본값은 이 컴퓨터에서 띄운 서버이고, GPU 서버에 모델을 올리면
   VITE_STYLEGAN_TARGET=http://<서버주소>:8766 으로 코드 수정 없이 옮겨 붙는다.
   원격 주소일 때는 Host 헤더를 서버 쪽으로 바꿔야 붙는 서버가 있어 changeOrigin 을 켠다. */
const styleGanTarget = process.env.VITE_STYLEGAN_TARGET ?? 'http://127.0.0.1:8766'
const coreTarget = process.env.VITE_CORE_TARGET ?? 'http://127.0.0.1:8767'
const isLocal = (target: string) => /^https?:\/\/(127\.0\.0\.1|localhost)(:|$)/.test(target)

const allowedHosts = (process.env.VITE_ALLOWED_HOSTS ?? 'deck-macmini.tailb3396d.ts.net')
  .split(',')
  .map((host) => host.trim())
  .filter(Boolean)

export default defineConfig({
  plugins: [react()],
  server: {
    host: process.env.VITE_DEV_HOST ?? '127.0.0.1',
    allowedHosts,
    proxy: {
      '/api/stylegan': {
        target: styleGanTarget,
        changeOrigin: !isLocal(styleGanTarget),
        rewrite: (path) => path.replace(/^\/api\/stylegan/, ''),
      },
      '/api/core': {
        target: coreTarget,
        changeOrigin: !isLocal(coreTarget),
        rewrite: (path) => path.replace(/^\/api\/core/, ''),
      },
    },
  },
})
