/**
 * Eater of Logs - Cloudflare Worker
 * Gestisce l'approvazione dei post via Telegram e triggera GitHub Actions per la pubblicazione.
 */

addEventListener('fetch', event => {
    event.respondWith(handleRequest(event.request))
})

// Variabili d'ambiente richieste nel Worker:
// GITHUB_TOKEN, GITHUB_OWNER, GITHUB_REPO, TELEGRAM_BOT_TOKEN

async function handleRequest(request) {
    if (request.method !== 'POST') {
        return new Response('Method not allowed', { status: 405 })
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

    // ── MODIFICA (rimuovi bottoni, chiedi di rispondere) ─────
    if (action === 'edit') {
        await answerCallback(callbackQuery.id, "Modalita' modifica")
        await editTelegramMessage(chatId, messageId,
            message.text + "\n\n--- IN MODIFICA ---\nRispondi a questo messaggio con il testo corretto, poi rilancia eater.py.", null)
        return new Response('OK')
    }

    // ── PUBBLICA (una o tutte le piattaforme) ────────────────
    if (action.startsWith('publish')) {
        let platforms = "all"
        if (action === "publish_twitter") platforms = "twitter"
        if (action === "publish_bluesky") platforms = "bluesky"
        if (action === "publish_telegram") platforms = "telegram"

        await answerCallback(callbackQuery.id, "Pubblicazione in corso...")

        const success = await triggerGitHubAction(anomalyId, platforms)

        if (success) {
            await editTelegramMessage(chatId, messageId,
                message.text + `\n\n--- APPROVATO (${platforms}) --- Invio in corso...`, null)
        } else {
            await editTelegramMessage(chatId, messageId,
                message.text + "\n\n--- ERRORE --- GitHub Action non triggerata.", null)
        }
    }

    return new Response('OK')
}

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
    // Rimuovi bottoni dopo l'azione (replyMarkup null = nessuna inline keyboard)
    if (replyMarkup !== undefined) {
        payload.reply_markup = replyMarkup ? replyMarkup : { inline_keyboard: [] }
    }
    await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    })
}
