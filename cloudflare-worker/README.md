# Fila assíncrona — deploy do Cloudflare Worker

Este Worker é a "recepção" sempre online: recebe o clique de "Gerar Posts" do
seu colega mesmo com seu computador desligado, guarda o pedido, e o
`engine-api` (rodando no seu computador) busca esse pedido periodicamente
quando está online.

Passos abaixo precisam da sua conta Cloudflare — não podem ser feitos por
mim, só documentados aqui.

## 1. Pré-requisitos

```bash
npm install -g wrangler
wrangler login
```

## 2. Criar o namespace KV

```bash
cd cloudflare-worker
wrangler kv namespace create QUEUE_KV
```

Isso imprime um `id`. Copie e cole em `wrangler.toml`, substituindo
`REPLACE_WITH_YOUR_KV_NAMESPACE_ID`.

## 3. Gerar e configurar o segredo compartilhado

Esse segredo autentica a comunicação `engine-api` ↔ Worker nas rotas
`/api/pending` e `/api/complete` (a rota `/api/request`, que o colega usa, é
pública de propósito — mesmo nível de confiança que o `/run` do engine-api
hoje).

```bash
openssl rand -hex 32
# copie o valor gerado
wrangler secret put QUEUE_SHARED_SECRET
# cole o mesmo valor quando pedido
```

Guarde esse mesmo valor para colocar em `QUEUE_SHARED_SECRET` no `.env` do
`engine-api` (fora do git).

## 4. Deploy

```bash
wrangler deploy
```

O comando imprime a URL pública (algo como
`https://logistik-queue.SEU-SUBDOMINIO.workers.dev`).

## 5. (Opcional) Domínio próprio

No painel Cloudflare → Workers & Pages → `logistik-queue` → Settings →
Domains & Routes → Add Custom Domain (ex.: `gerar.logaid.ai`). Fica mais
apresentável para mandar ao colega do que a URL `.workers.dev`.

## 6. Configurar o `engine-api`

No `.env` do projeto (não versionado):

```
QUEUE_WORKER_URL=https://gerar.logaid.ai        # ou a URL .workers.dev
QUEUE_SHARED_SECRET=<o mesmo valor do passo 3>
QUEUE_POLL_INTERVAL_SECONDS=60
```

Depois: `docker compose build engine-api && docker compose up -d engine-api`.

## 7. Testar

1. Com o `engine-api` **desligado**, abra a URL do Worker e clique em
   "🚀 Gerar Posts" — deve mostrar "⏳ Pedido registrado...".
2. Suba o `engine-api` (`docker compose up -d`). Dentro de
   `QUEUE_POLL_INTERVAL_SECONDS` (60s por padrão), os logs
   (`docker compose logs -f engine-api`) devem mostrar
   `Queue: pending request found ... running pipeline`.
3. Quando terminar, recarregue a página do Worker — deve mostrar
   "✅ Pronto! N posts criados."

## Sobre segurança

`/api/request` é público (qualquer um com o link pode clicar). Isso é
intencional e consistente com o `/run` do `engine-api`, que também não tem
autenticação hoje. Se quiser restringir quem pode acionar o botão, a forma
mais simples é colocar **Cloudflare Access** na frente da rota deste Worker
(mesma ideia já usada nos hostnames do Cloudflare Tunnel) — assim só quem
fizer login (e-mail/OTP) vê a página e consegue clicar.
