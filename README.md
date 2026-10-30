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
- Mapeamento persistente e snapshots no Supabase PostgreSQL para evitar duplicações e detectar alterações nos dois sentidos.
- Cargos: criação e edição Discord ↔ GoLive (nome, cor, destaque, menção e permissões com equivalência entre as plataformas).
- Comandos administrativos no Discord: `/bridge_status` mostra o estado, `/bridge_sync` solicita sincronização e `/bridge_config` abre um painel interativo para ativar/desativar salas automáticas e escolher o canal de compartilhamento. Exigem Gerenciar Servidor.
- Salas GoLive para calls: ao entrar o primeiro participante humano em um canal de voz, o bot cria uma sala GoLive e publica um link uma vez por sessão no canal de texto escolhido no painel. Não envia uma mensagem para cada participante.
- Vínculo individual Discord ↔ GoLive por código de uso único: `/vincular` no Discord envia o código por mensagem privada; depois, no GoLive, o usuário digita `/vincular XXX-XXX` em uma sala do grupo conectado. `/vinculo` consulta a associação.

## Proteção dos canais existentes

O VoidBridge **não apaga categorias, canais nem cargos automaticamente**. Canais extras são preservados. A primeira sincronização associa canais existentes pelo nome e tipo; depois salva os IDs no Supabase. Para cargos, a associação inicial usa nome único e depois mantém os IDs e o último estado sincronizado. Se houver nomes repetidos, confira a associação inicial. Se um cargo mapeado for apagado em uma plataforma, o bot não o recria nem apaga o equivalente automaticamente.

## Salas GoLive para calls Discord

1. Execute `/bridge_config` como administrador do servidor.
2. Mantenha as salas automáticas ativadas.
3. Escolha um canal de texto onde o bot tenha permissão para enviar mensagens.
4. Quando alguém entrar em um canal de voz vazio, o VoidBridge cria uma sala GoLive privada/não listada e publica o link nesse canal de texto. O link é compartilhado uma vez por sessão de call, não a cada entrada de participante.

O botão do painel permite desativar o recurso a qualquer momento. A sala GoLive é separada da chamada Discord: não há retransmissão de áudio, vídeo ou tela do Discord pelo bot.

## Configuração

Edite config.json (o repositório já contém os IDs informados):

```json
{
  "golive_group_id": "qrv8a6r6qm",
  "discord_group_id": "1411504431991750718"
}
```

Tokens continuam no arquivo `.env`: `DISCORD_TOKEN` e `GOLIVE_TOKEN`. Não é necessário criar aplicativo OAuth nem configurar Client ID, Client Secret ou URL de callback.

## Instalação no Windows

1. Baixe por Code → Download ZIP e extraia.
2. Instale Python 3.12: https://www.python.org/downloads/ (marque Add Python to PATH).
3. Execute INSTALAR.bat.
4. Renomeie .env.example para .env e preencha `DISCORD_TOKEN`, `GOLIVE_TOKEN` e `DATABASE_URL` (URI PostgreSQL do Supabase).
5. Execute INICIAR.bat.

## Permissões necessárias

**Discord:** View Channels, Send Messages, Read Message History, Attach Files, **Manage Channels** e **Manage Roles**. Ative Message Content Intent no Developer Portal.

**GoLive:** o bot deve estar no grupo e ter acesso de leitura e escrita, além de `manageChannels` para categorias/salas e `manageRoles` para criar/editar cargos. O GoLive só aceita no cargo as permissões que o próprio bot possui.

## Limitações

O VoidBridge não retransmite áudio das chamadas Discord para o GoLive. As salas GoLive criadas para as calls são salas independentes: as pessoas precisam abrir o link e participar por conta própria. A documentação oficial informa que bots não entram em chamadas de voz de grupos GoLive; o bot usa apenas o recurso experimental de salas ao vivo para criar/ocupar a sala e compartilhar o endereço. O bot pode aparecer como participante BOT. O protocolo de salas ao vivo pode mudar sem aviso.

A criação e o compartilhamento automáticos dependem de um canal de texto escolhido no painel `/bridge_config`. Se nenhum canal for selecionado, a sala ainda pode ser criada, mas o link não será publicado. Os nomes das salas usam um identificador estável e prefixo `priv-`; isso evita listagem pública, mas qualquer pessoa com o link ou nome pode entrar.

A sincronização de cargos cobre nome, cor, destaque, menção e permissões equivalentes; algumas permissões não têm correspondência exata e ficam de fora. A ordem dos cargos e a atribuição de cargos aos membros ainda não são sincronizadas. Exclusões não são espelhadas automaticamente para evitar apagar dados por engano. O código de vinculação associa as identidades sem guardar tokens de usuário. Não espelha presença, reações ou threads. Canais de fórum, palco e outros tipos especiais do Discord não são copiados. Mensagens antigas não são importadas; a sincronização começa quando o bot está online. O serviço precisa permanecer ativo.

## Segurança

Nunca publique o arquivo .env ou tokens. Se um token vazar, revogue-o e gere outro. Use tokens oficiais de bots.

## Documentação

- GoLive Developers: https://developers-golive.nemtudo.me/
- API REST: https://developers-golive.nemtudo.me/referencia/rest
- Eventos: https://developers-golive.nemtudo.me/referencia/eventos
- Discord Developer Portal: https://discord.com/developers/applications
