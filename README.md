# VoidBridge — Discord ↔ GoLive

Ponte de mensagens entre canais de texto do Discord e salas de texto de grupos do GoLive. Feito em Python para Windows 10/11.

## O que esta versão faz

- Sincroniza mensagens de texto nos dois sentidos.
- Encaminha imagens e arquivos anexados em mensagens (upload/download, respeitando os limites das plataformas).
- Sincroniza exclusões de mensagens quando o mapeamento existe.
- Atualiza mensagens editadas nos dois sentidos (Discord ↔ GoLive).
- Sincroniza edições nos dois sentidos usando a rota PATCH de mensagens do GoLive.
- Registra entrada/saída de voz do Discord como aviso de texto opcional.
- Reconecta ao Gateway do GoLive automaticamente, respeitando erros temporários (`retryable`) e parando em recusas definitivas.
- Repete a verificação inicial da API quando o GoLive informa indisponibilidade temporária (`503` com `retryable: true`).
- Fecha corretamente as conexões SQLite após cada operação, evitando acúmulo de conexões abertas.
- Usa SQLite para mapear mensagens e evitar loops.
- Suporta vários pares de canais.

## Limites

Não retransmite áudio, câmera, compartilhamento de tela nem chamadas. Também não espelha cargos, permissões, contas, listas de membros, presença, reações ou threads. Mensagens antigas não são importadas; a ponte começa quando está online. Recursos dependem das permissões e limites de cada plataforma.

## Instalação no Windows

1. Baixe o projeto por **Code → Download ZIP** e extraia.
2. Instale Python 3.12 em https://www.python.org/downloads/ e marque **Add Python to PATH**. O iniciador usa Python 3.12 se estiver instalado; caso contrário, tenta a versão padrão do Python.
3. Execute `INSTALAR.bat`.
4. Renomeie `.env.example` para `.env` e preencha os dois tokens.
5. Edite `config.json` com os IDs dos canais.
6. Execute `INICIAR.bat`.

## Criar o bot Discord

1. Acesse https://discord.com/developers/applications e crie uma Application.
2. Em **Bot**, crie/reset o token e coloque-o em `DISCORD_TOKEN`.
3. Em **Bot → Privileged Gateway Intents**, habilite **Message Content Intent**.
4. Em **OAuth2 → URL Generator**, marque `bot`.
5. Permissões recomendadas: View Channels, Send Messages, Read Message History, Attach Files e Embed Links.
6. Use o link gerado para adicionar o bot ao servidor.
7. Ative o Modo Desenvolvedor no Discord e copie o ID do canal.

## Criar e adicionar o bot GoLive

1. Acesse https://golive.nemtudo.me/developers.
2. Crie um bot e copie o token para `GOLIVE_TOKEN`. O portal copia o valor com o prefixo `Bot `; o VoidBridge também aceita o token sem esse prefixo e normaliza automaticamente. Não publique esse token.
3. Use a aba **Instalação** do portal para adicioná-lo ao grupo.
4. Abra a sala de texto no navegador. O endereço tem o formato `/groups/ID_DO_GRUPO/ID_DA_SALA`.
5. Copie os IDs para `config.json`.

O GoLive usa `Authorization: Bot <token>`, Gateway `wss://apigolive.nemtudo.me/ws`, evento `group-message` e envio por `POST /groups/:id/channels/:cid/messages`.

## Configurar os canais

Em `config.json`, cada objeto de `bridges` liga um canal Discord a uma sala GoLive:

```json
{
  "bridges": [
    {
      "discord_channel_id": "123456789012345678",
      "golive_group_id": "ID_DO_GRUPO",
      "golive_channel_id": "ID_DA_SALA"
    }
  ]
}
```

Adicione outros objetos para conectar mais canais. Use salas de texto, não canais de voz.

## Arquivos

- `main.py`: bot e sincronização.
- `config.json`: IDs e opções.
- `.env.example`: modelo para tokens.
- `requirements.txt`: dependências.
- `INSTALAR.bat` e `INICIAR.bat`: instalação e execução.
- `logs/voidbridge.log`: logs (criado ao executar).
- `state.sqlite3`: banco local (criado ao executar).

O PC precisa ficar ligado e conectado à internet para o bot funcionar.

## Segurança

- Nunca envie `.env` ao GitHub; ele está no `.gitignore`.
- Não cole tokens em prints, mensagens ou issues.
- Se um token vazar, revogue-o e gere outro.
- Use somente tokens oficiais de bots, nunca tokens de usuário.
- Comece testando em canais privados e com permissões mínimas.

## Solução de problemas

- **Discord não lê mensagens:** confira Message Content Intent no Developer Portal.
- **401 GoLive:** confira se copiou o token do bot na aba Token do Portal do Desenvolvedor, sem `Bearer`. O prefixo `Bot ` é aceito com ou sem ele no `.env`. Se gerou um token novo, atualize o `.env`.
- **Bot não recebe eventos:** confirme que foi adicionado ao grupo e tem acesso à sala.
- **403 ao enviar:** confira a permissão `sendMessages` do bot na sala GoLive.
- **Anexo não encaminha:** confira Attach Files no Discord e permissões/limites de upload no GoLive.
- **PC desligado:** o bot local para de funcionar.

## Documentação

- GoLive Developers: https://developers-golive.nemtudo.me/
- Primeiro bot: https://developers-golive.nemtudo.me/guia/primeiro-bot
- Eventos WebSocket: https://developers-golive.nemtudo.me/referencia/eventos
- API REST: https://developers-golive.nemtudo.me/referencia/rest
- Discord Developer Portal: https://discord.com/developers/applications
