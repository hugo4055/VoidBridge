# VoidBridge — Discord ↔ GoLive

Sincroniza a estrutura de um servidor Discord inteiro com um grupo GoLive usando somente os dois IDs em config.json. **Não retransmite áudio.**

## O que sincroniza

- Categorias e nomes de canais.
- Criação automática de categorias e canais ausentes nos dois lados.
- Canais de texto e voz (estrutura e nomes, sem áudio).
- Renomeações nos dois sentidos.
- Mensagens de texto nos canais associados, nos dois sentidos.
- Anexos e imagens dentro dos limites das plataformas.
- Edições e exclusões de mensagens quando há mapeamento.
- Mapeamento persistente em SQLite para reduzir duplicações.
- Vínculo individual Discord ↔ GoLive por OAuth2 (`/vincular`) e consulta (`/vinculo`); o callback usa `state` e PKCE e guarda apenas os IDs das contas, sem persistir tokens OAuth.

## Proteção dos canais existentes

O VoidBridge **não apaga categorias nem canais**. Canais extras são preservados. A primeira sincronização associa canais existentes pelo nome e tipo; depois salva os IDs no banco local. Se houver nomes repetidos, confira a associação inicial. A sincronização de movimentação entre categorias ainda não está incluída nesta versão.

## Configuração

Edite config.json (o repositório já contém os IDs informados):

```json
{
  "golive_group_id": "qrv8a6r6qm",
  "discord_group_id": "1411504431991750718"
}
```

Tokens continuam no arquivo `.env`: `DISCORD_TOKEN` e `GOLIVE_TOKEN`. Para o vínculo de contas, configure também `GOLIVE_OAUTH_CLIENT_ID`, `GOLIVE_OAUTH_CLIENT_SECRET` e `GOLIVE_OAUTH_REDIRECT_URI` no ambiente do servidor. O callback padrão é `https://voidbridge.onrender.com/oauth/golive/callback`.

## Instalação no Windows

1. Baixe por Code → Download ZIP e extraia.
2. Instale Python 3.12: https://www.python.org/downloads/ (marque Add Python to PATH).
3. Execute INSTALAR.bat.
4. Renomeie .env.example para .env e preencha os dois tokens.
5. Execute INICIAR.bat.

## Permissões necessárias

**Discord:** View Channels, Send Messages, Read Message History, Attach Files e **Manage Channels**. Ative Message Content Intent no Developer Portal.

**GoLive:** o bot deve estar no grupo e ter acesso de leitura e escrita, além da permissão manageChannels para criar/renomear categorias e salas. A API do GoLive limita o grupo a 50 canais.

## Limitações

Sem áudio, câmera, compartilhamento de tela ou chamadas. A sincronização de cargos e permissões ainda não está incluída; o OAuth apenas associa as identidades de forma segura. Não espelha presença, reações ou threads. Canais de fórum, palco e outros tipos especiais do Discord não são copiados. Mensagens antigas não são importadas; a sincronização começa quando o bot está online. O computador que executa o bot precisa permanecer ligado.

## Segurança

Nunca publique o arquivo .env ou tokens. Se um token vazar, revogue-o e gere outro. Use tokens oficiais de bots.

## Documentação

- GoLive Developers: https://developers-golive.nemtudo.me/
- API REST: https://developers-golive.nemtudo.me/referencia/rest
- Eventos: https://developers-golive.nemtudo.me/referencia/eventos
- Discord Developer Portal: https://discord.com/developers/applications
