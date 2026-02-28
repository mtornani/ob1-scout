/**
 * Eater of Logs - Cloudflare Worker v2
 *
 * Gestisce l'approvazione dei post via Telegram:
 * - Twitter/X: pubblica DIRETTAMENTE dal Worker (bypassa blocco IP GitHub Actions)
 * - Bluesky + Telegram: triggera GitHub Actions come prima
 *
 * Env vars richieste nel dashboard Cloudflare:
 *   TELEGRAM_BOT_TOKEN
 *   GITHUB_TOKEN, GITHUB_OWNER, GITHUB_REPO
 *   X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET
 */

addEventListener('fetch', event => {
    event.respondWith(handleRequest(event.request))
})

// ══════════════════════════════════════════════════════════════
// OAUTH 1.0a PER TWITTER (Web Crypto API)
// ══════════════════════════════════════════════════════════════

function generateNonce() {
    const array = new Uint8Array(16)
    crypto.getRandomValues(array)
    return Array.from(array, b => b.toString(16).padStart(2, '0')).join('')
}

/**
 * RFC 3986 percent-encoding (richiesto da OAuth 1.0a).
 * encodeURIComponent non codifica: - _ . ~ (corretto per RFC 3986)
 * ma non codifica neanche ! * ' ( ) che OAuth richiede codificati.
 */
function percentEncode(str) {
    return encodeURIComponent(str)
        .replace(/!/g, '%21')
        .replace(/\*/g, '%2A')
        .replace(/'/g, '%27')
        .replace(/\(/g, '%28')
        .replace(/\)/g, '%29')
}

async function hmacSha1(key, data) {
    const cryptoKey = await crypto.subtle.importKey(
        'raw',
        new TextEncoder().encode(key),
        { name: 'HMAC', hash: 'SHA-1' },
        false,
        ['sign']
    )
    const sig = await crypto.subtle.sign(
        'HMAC',
        cryptoKey,
        new TextEncoder().encode(data)
    )
    return btoa(String.fromCharCode(...new Uint8Array(sig)))
}

/**
 * Costruisce l'header Authorization OAuth 1.0a.
 * Per POST con body JSON, i parametri del body NON entrano nella firma
 * (solo oauth_* params + query params).
 */
async function buildOAuthHeader(method, url, consumerKey, consumerSecret, token, tokenSecret) {
    const oauthParams = {
        oauth_consumer_key: consumerKey,
        oauth_nonce: generateNonce(),
        oauth_signature_method: 'HMAC-SHA1',
        oauth_timestamp: Math.floor(Date.now() / 1000).toString(),
        oauth_token: token,
        oauth_version: '1.0'
    }

    // Signature base string
    const paramString = Object.keys(oauthParams).sort()
        .map(k => `${percentEncode(k)}=${percentEncode(oauthParams[k])}`)
        .join('&')

    const baseString = `${method}&${percentEncode(url)}&${percentEncode(paramString)}`
    const signingKey = `${percentEncode(consumerSecret)}&${percentEncode(tokenSecret)}`

    oauthParams.oauth_signature = await hmacSha1(signingKey, baseString)

    return 'OAuth ' + Object.keys(oauthParams).sort()
        .map(k => `${percentEncode(k)}="${percentEncode(oauthParams[k])}"`)
        .join(', ')
}

// ══════════════════════════════════════════════════════════════
// TWITTER: PUBBLICAZIONE DIRETTA
// ══════════════════════════════════════════════════════════════

async function publishTweet(text) {
    const url = 'https://api.twitter.com/2/tweets'

    try {
        const authHeader = await buildOAuthHeader(
            'POST', url,
            X_API_KEY, X_API_SECRET,
            X_ACCESS_TOKEN, X_ACCESS_SECRET
        )

        const response = await fetch(url, {
            method: 'POST',
            headers: {
                'Authorization': authHeader,
                'Content-Type': 'application/json',
                'User-Agent': 'OB1Scout/1.0'
            },
            body: JSON.stringify({ text })
        })

        const body = await response.text()

        if (response.ok) {
            const data = JSON.parse(body)
            return { success: true, tweetId: data.data?.id }
        }
        return { success: false, error: `HTTP ${response.status}: ${body.substring(0, 200)}` }
    } catch (err) {
        return { success: false, error: err.message }
    }
}

// ══════════════════════════════════════════════════════════════
// FETCH DATI DA GITHUB + FORMATTAZIONE TWEET
// ══════════════════════════════════════════════════════════════

const OPP_SHORT = {
    'svincolato': 'SVINCOLATO',
    'rescissione': 'IN USCITA',
    'prestito': 'IN PRESTITO',
    'talento_serie_d': 'TALENTO SERIE D',
    'infortunio': 'MERCATO APERTO',
    'anomalia': 'ANOMALIA'
}

function normalizeAnomaly(raw, index) {
    const id = raw.id !== undefined ? raw.id : index + 1

    // Formato Serie C (annidato con player_profile)
    if (raw.player_profile) {
        const p = raw.player_profile
        const year = new Date().getFullYear()
        return {
            id,
            player_name: raw.player_name || p.name,
            age: p.birth_year ? year - p.birth_year : null,
            role: p.role || 'N/D',
            current_club: p.current_club || 'N/D',
            opportunity_type: raw.opportunity_type || 'anomalia',
            description: raw.description || '',
            clubs_involved: raw.clubs_involved || [],
            relevance_score: raw.relevance_score || 0,
            tactical_fit: raw.tactical_fit || 0
        }
    }

    // Formato Global (flat con raw_content)
    return {
        id,
        player_name: raw.player_name,
        age: null,
        role: 'N/D',
        current_club: 'N/D',
        opportunity_type: 'anomalia',
        description: raw.raw_content || '',
        clubs_involved: [],
        relevance_score: raw.score || 0,
        tactical_fit: 0
    }
}

/**
 * Conta caratteri come Twitter:
 * - URL → sempre 23 chars
 * - Emoji (Symbol, Other) → 2 chars
 * - Resto → 1 char
 */
function twitterCharCount(text) {
    const urls = text.match(/https?:\/\/\S+/g) || []
    const clean = text.replace(/https?:\/\/\S+/g, '')
    let count = 0
    for (const ch of clean) {
        const code = ch.codePointAt(0)
        if (
            (code >= 0x1F000 && code <= 0x1FFFF) ||   // Emoticons, symbols, etc.
            (code >= 0x2600 && code <= 0x27BF) ||      // Misc symbols, dingbats
            (code >= 0x2700 && code <= 0x27BF) ||      // Dingbats
            (code >= 0xFE00 && code <= 0xFE0F) ||      // Variation selectors
            (code >= 0x1F900 && code <= 0x1F9FF)       // Supplemental symbols
        ) {
            count += 2
        } else {
            count += 1
        }
    }
    return count + urls.length * 23
}

function formatTweet(anomaly, rawAnomalies) {
    const now = new Date()
    const dd = String(now.getDate()).padStart(2, '0')
    const mm = String(now.getMonth() + 1).padStart(2, '0')
    const date = `${dd}/${mm}`

    // Stats
    const total = rawAnomalies.length
    const year = now.getFullYear()
    let under28 = rawAnomalies.filter(a => {
        const by = a.player_profile?.birth_year
        return by && (year - by) < 28
    }).length
    if (under28 === 0) under28 = Math.round(total * 0.4)

    const name = anomaly.player_name || 'Giocatore Ignoto'
    const age = anomaly.age || 'N/D'
    const oppShort = OPP_SHORT[anomaly.opportunity_type] || anomaly.opportunity_type || 'N/D'
    const clubs = anomaly.clubs_involved?.length > 0
        ? ` \u2192 ${anomaly.clubs_involved.join(', ')}` : ''
    const desc = anomaly.description || ''

    const LIMIT = 280
    const header = `Lega Pro ${date}. ${total} svincolati, ${under28} U28.\n\n`
    const playerLine = `${name}, ${age}. ${oppShort}${clubs}.\n`
    const footer = `\nhttps://t.me/Ob1LegaPro_bot`

    const fixedLen = twitterCharCount(header + playerLine + footer)
    const budget = LIMIT - fixedLen - 4 // margine per "...\n"

    let detailLine = ''
    if (budget > 0 && desc) {
        if (twitterCharCount(desc) <= budget) {
            detailLine = `${desc}\n`
        } else {
            let t = desc
            while (t && twitterCharCount(t) > budget) {
                t = t.slice(0, -1)
            }
            detailLine = `${t.trimEnd()}...\n`
        }
    }

    return header + playerLine + detailLine + footer
}

/**
 * Scarica data.json dal repo GitHub e formatta il tweet per l'anomalia richiesta.
 * Usa l'API GitHub con token per supportare repo privati.
 */
async function fetchDataAndFormatTweet(anomalyId) {
    const url = `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/contents/eater-of-logs/data.json`

    try {
        const response = await fetch(url, {
            headers: {
                'Authorization': `token ${GITHUB_TOKEN}`,
                'Accept': 'application/vnd.github.v3.raw+json',
                'User-Agent': 'OB1Scout-Worker/1.0'
            }
        })

        if (!response.ok) {
            return { success: false, error: `Fetch data.json: HTTP ${response.status}` }
        }

        const rawData = await response.json()
        const anomalies = Array.isArray(rawData) ? rawData : rawData.anomalies || [rawData]

        // Normalizza
        const normalized = anomalies.map((a, i) => normalizeAnomaly(a, i))

        // Trova target
        const target = normalized.find(a => String(a.id) === String(anomalyId))
        if (!target) {
            return { success: false, error: `Anomalia ID ${anomalyId} non trovata in ${anomalies.length} anomalie` }
        }

        const tweet = formatTweet(target, anomalies)
        return { success: true, text: tweet }
    } catch (err) {
        return { success: false, error: `Fetch/format: ${err.message}` }
    }
}

// ══════════════════════════════════════════════════════════════
// HANDLER PRINCIPALE
// ══════════════════════════════════════════════════════════════

async function handleRequest(request) {
    if (request.method !== 'POST') {
        return new Response('Eater of Logs Worker v2 — POST only', { status: 405 })
    }

    try {
        const update = await request.json()
        if (update.callback_query) {
            return await handleCallbackQuery(update.callback_query)
        }
        return new Response('OK')
    } catch (err) {
        return new Response(err.stack, { status: 500 })
    }
}

async function handleCallbackQuery(callbackQuery) {
    const data = callbackQuery.data
    const message = callbackQuery.message
    const chatId = message.chat.id
    const messageId = message.message_id
    const [action, anomalyId] = data.split(':')

    // ── SCARTA ───────────────────────────────────────────────
    if (action === 'reject') {
        await answerCallback(callbackQuery.id, "Scartato")
        await editTelegramMessage(chatId, messageId,
            message.text + "\n\n--- SCARTATO ---", null)
        return new Response('OK')
    }

    // ── MODIFICA ─────────────────────────────────────────────
    if (action === 'edit') {
        await answerCallback(callbackQuery.id, "Modalita' modifica")
        await editTelegramMessage(chatId, messageId,
            message.text + "\n\n--- IN MODIFICA ---\nRispondi a questo messaggio con il testo corretto, poi rilancia eater.py.", null)
        return new Response('OK')
    }

    // ── PUBBLICA ─────────────────────────────────────────────
    if (action.startsWith('publish')) {
        await answerCallback(callbackQuery.id, "Pubblicazione in corso...")

        const results = []

        if (action === 'publish_twitter') {
            // Solo Twitter → pubblica dal Worker
            results.push(await handleTwitterPublish(anomalyId))

        } else if (action === 'publish_all') {
            // Twitter dal Worker + Bluesky,Telegram da GitHub Actions (in parallelo)
            const [twitterResult, ghSuccess] = await Promise.all([
                handleTwitterPublish(anomalyId),
                triggerGitHubAction(anomalyId, 'bluesky,telegram')
            ])
            results.push(twitterResult)
            results.push(ghSuccess
                ? '\u2705 Bluesky+Telegram: GitHub Action triggerata'
                : '\u274C Bluesky+Telegram: GitHub Action fallita')

        } else {
            // Solo Bluesky o Telegram → GitHub Actions
            const platforms = action === 'publish_bluesky' ? 'bluesky' : 'telegram'
            const success = await triggerGitHubAction(anomalyId, platforms)
            results.push(success
                ? `\u2705 ${platforms}: GitHub Action triggerata`
                : `\u274C ${platforms}: GitHub Action fallita`)
        }

        const statusText = results.join('\n')
        await editTelegramMessage(chatId, messageId,
            message.text + `\n\n--- APPROVATO ---\n${statusText}`, null)
    }

    return new Response('OK')
}

/**
 * Gestisce la pubblicazione Twitter: fetch dati → formatta tweet → pubblica.
 */
async function handleTwitterPublish(anomalyId) {
    // Verifica credenziali disponibili
    if (typeof X_API_KEY === 'undefined' || !X_API_KEY) {
        return '\u274C Twitter: credenziali X non configurate nel Worker'
    }

    // 1. Fetch data.json e formatta tweet
    const formatted = await fetchDataAndFormatTweet(anomalyId)
    if (!formatted.success) {
        return `\u274C Twitter: ${formatted.error}`
    }

    // 2. Pubblica su Twitter
    const result = await publishTweet(formatted.text)
    if (result.success) {
        return `\u2705 Twitter: pubblicato (ID: ${result.tweetId})`
    }
    return `\u274C Twitter: ${result.error}`
}

// ══════════════════════════════════════════════════════════════
// GITHUB ACTIONS (per Bluesky + Telegram)
// ══════════════════════════════════════════════════════════════

async function triggerGitHubAction(anomalyId, platforms) {
    const url = `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/actions/workflows/publish-eater.yml/dispatches`

    try {
        const response = await fetch(url, {
            method: 'POST',
            headers: {
                'Authorization': `token ${GITHUB_TOKEN}`,
                'Accept': 'application/vnd.github.v3+json',
                'User-Agent': 'Cloudflare-Worker'
            },
            body: JSON.stringify({
                ref: 'main',
                inputs: {
                    anomaly_id: anomalyId,
                    platforms: platforms
                }
            })
        })
        return response.status === 204
    } catch (err) {
        return false
    }
}

// ══════════════════════════════════════════════════════════════
// TELEGRAM HELPERS
// ══════════════════════════════════════════════════════════════

async function answerCallback(callbackQueryId, text) {
    const url = `https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/answerCallbackQuery`
    await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            callback_query_id: callbackQueryId,
            text: text
        })
    })
}

async function editTelegramMessage(chatId, messageId, text, replyMarkup) {
    const url = `https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/editMessageText`
    const payload = {
        chat_id: chatId,
        message_id: messageId,
        text: text
    }
    if (replyMarkup !== undefined) {
        payload.reply_markup = replyMarkup ? replyMarkup : { inline_keyboard: [] }
    }
    await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    })
}
