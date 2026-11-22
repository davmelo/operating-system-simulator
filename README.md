# Simulador de Sistema Operacional Modular

Projeto de Sistemas Operacionais em Python, dividido em 5 etapas:

1. **Sistema de Arquivos** (`fs/`)
2. **Entrada/Saída e Armazenamento** (`io/`: braço de disco, RAID, SSD)
3. Segurança e Proteção (usuários, ACL, criptografia, anti-vírus)
4. **Gerenciamento de Memória** (`memory/`: área reservada contígua, paginação, swap)
5. Gerenciamento de Processos

## Como rodar (Etapa 1)

```
python -m so\\\_simulator --strategy indexada            # ou contigua
python -m so\\\_simulator --strategy contigua --blocks 8 --block-size 4096
```

Digite `help` para ver os comandos: `mkdir`, `create`, `ls`, `tree`, `cd`, `pwd`, `cat`,
`write`, `append`, `mv`, `rm`, `rmdir`, `compact`, `fs\\\_status \\\[--details]`.

## Estrutura da Etapa 1

* `fs/blockdevice.py`: disco em blocos (`read\\\_block` / `write\\\_block`)
* `fs/allocators.py`: estratégias de alocação (contígua e indexada)
* `fs/filesystem.py`: caminhos absolutos/relativos e CRUD
* `fs/diagnostico.py`: mapa do disco e relatório de fragmentação
* `fs/comandos.py`: comandos do shell; `shell.py`: shell compartilhado entre as etapas

## Exemplo

```
> mkdir /sistema
> mkdir /sistema/notas
> create /sistema/notas/teste\\\_simulador.txt --size 10KB
> write /sistema/notas/teste\\\_simulador.txt --text "ola mundo"
> fs\\\_status --details
Tamanho do Bloco de Disco: 4 KB | Total de Blocos: 16 (64 KB)

\\\[MAPA DE ALOCAÇÃO DO DISCO (Indexada)]
Bloco 00 \\\[INODE\\\_RAIZ]  : Diretório '/'
Bloco 01 \\\[INODE]       : Arquivo '/sistema/notas/teste\\\_simulador.txt'
Bloco 02 \\\[DADO]        : '/sistema/notas/teste\\\_simulador.txt' (Bloco 1 - 9 bytes ocupados)
Bloco 03..15           : \\\[LIVRE]

Mapa visual: \\\[RiA.............]
  A = /sistema/notas/teste\\\_simulador.txt

--- RELATÓRIO DE FRAGMENTAÇÃO ---
Arquivo 'teste\\\_simulador.txt': real 9 bytes (9 B) | alocado 4096 bytes (1 blocos de dados + 1 de índice) | frag. interna 4087 bytes (100% de desperdício no último bloco)
Fragmentação Interna total       : 4087 bytes
Fragmentação Externa             : 0 bytes
Necessidade de Compactação       : NÃO (alocação indexada)
```

## Etapa 2: Entrada/Saída e Armazenamento

```
python -m so\\\_simulator --io-algo sstf                          # disco único
python -m so\\\_simulator --raid 5 --disks 3 --io-algo sstf       # RAID 5 com 3 discos
python -m so\\\_simulator --device ssd --ssd-wl static            # SSD com Wear Leveling
python -m unittest discover -s tests -t .                      # testes
```

Camadas: `FileSystem → InputOutput → RAIDArray (discos com trilhas) | SSD (FTL)`.
O FS não mudou: `InputOutput` tem a interface do `BlockDevice`.

* `io/input\\\_output.py`: fila de E/S; escritas são aplicadas e escalonadas no fim de cada comando
* `io/scheduler.py`: FCFS, SSTF, SCAN, C-SCAN (+ LOOK e C-LOOK); o retorno do C-SCAN conta no deslocamento
* `io/physical\\\_disk.py`: disco com trilhas e cabeça (os blocos ficam espalhados pelas trilhas, 0..199)
* `io/raid.py`: disco único, RAID 0, 1, 4, 5 (paridade XOR, escrita completa de faixa ou read-modify-write, modo degradado, rebuild)
* `io/ssd.py`: FTL por páginas, escrita fora-do-lugar, TRIM, coleta de lixo, Wear Leveling dinâmico e estático
* `io/comandos.py`: comandos do shell

Comandos: `write\\\_file`, `read\\\_file`, `io\\\_sched`, `io\\\_algo`, `io\\\_log`, `io\\\_status`, `io\\\_device`,
`raid\\\_mode`, `raid\\\_status`, `raid\\\_fail`, `raid\\\_rebuild`, `raid\\\_check`, `raid\\\_test`, `ssd\\\_status`, `ssd\\\_demo`.

### Exemplo 2.1: escalonamento SSTF

```
> io\\\_sched --tracks 98,183,37,122,14 --head 53 --algo sstf
--- ESCALONAMENTO DE DISCO: SSTF (Shortest Seek Time First) ---
Ordem de Atendimento : 53 -> 37 -> 14 -> 98 -> 122 -> 183
Deslocamento da Cabeça: |53-37| + |37-14| + |14-98| + |98-122| + |122-183|
Total de Movimento    : 16 + 23 + 84 + 24 + 61 = 208 trilhas
```

`--algo all` compara todos os algoritmos. Cada `write\\\_file`/`read\\\_file`/`cat` também mostra o
escalonamento das suas requisições.

### Exemplo 2.2: gravação sob RAID 5

```
> write\\\_file /dados/planilha.csv --size 8KB --teste-falha
Arranjo Ativo: RAID 5 (3 Discos Físicos, Blocos de 4 KB)
\\\[E/S - DISTRIBUIÇÃO EM DISCO]
Disco 1: \\\[Bloco 02: Dados de 'planilha.csv' - Parte 1 (4 KB)]
Disco 2: \\\[PARIDADE : XOR(Bloco 02, Bloco 03)]
Disco 3: \\\[Bloco 03: Dados de 'planilha.csv' - Parte 2 (4 KB)]
\\\[TESTE DE FALHA SIMULADO]
\\\* Falha forçada no Disco 3!
\\\* Recuperando Bloco 03 via operação XOR: (Disco 1 XOR Disco 2) -> Reconstrução \\\[OK]
```

(Os blocos 02 e 03 são os de dados do arquivo no FS; a paridade cai no Disco 2 porque
a faixa é a 1 e a paridade do RAID 5 gira entre os discos.)

### SSD

`io\\\_device ssd` troca o armazenamento mantendo os dados. `ssd\\\_status` mostra páginas
(V/I/L) e apagamentos por bloco; `ssd\\\_demo` compara sem Wear Leveling, dinâmico e dinâmico+estático.

### Simplificações

Leituras são atendidas na hora e escritas no fim do comando; o escalonador reordena só o
atendimento físico (os dados seguem a ordem do programa). Liberar bloco (`rm`) não gera E/S no
HDD/RAID (só metadado) e vira TRIM no SSD. Blocos nunca gravados leem zeros sem E/S.

## Etapa 3: Segurança e Proteção

O módulo `security/` integra autenticação, privilégios, permissões Unix, criptografia
simulada em repouso e auditoria de integridade ao FS e à camada de E/S.

Comandos (a sessão inicial é `root`, senha `root`):

```
login --user root --password root
useradd carlos MinhaSenha devs
logout
login --user carlos --password MinhaSenha
mkdir /home/carlos/documentos
write\\\_file /home/carlos/documentos/segredo.txt --text "meu conteúdo"
read\\\_file /home/carlos/documentos/segredo.txt
cat /home/carlos/documentos/segredo.txt
logout
login --user root --password root
chmod /sistema/config.sys 750
encrypt\\\_file /home/carlos/segredo.txt --key MinhaChave123
logout
login --user carlos --password MinhaSenha
read\\\_file /home/carlos/segredo.txt --key MinhaChave123
logout
login --user root --password root
decrypt\\\_file /home/carlos/segredo.txt --key MinhaChave123
antivirus\\\_scan /
security\\\_log
```

`useradd` e `groupadd` exigem root. Ao cadastrar um usuário, o simulador cria
`/home/<usuário>` com proprietário e grupo corretos (permissão inicial `750`).
Usuários comuns podem criar arquivos e subdiretórios dentro de seu próprio diretório
pessoal, mas não diretamente em `/home` ou `/`. A travessia de cada diretório do
caminho exige permissão `x`; criar/remover entradas exige `wx` no diretório pai.
`chmod`, criptografia e operações globais de manutenção/alteração de RAID/SSD também
exigem MODO\_KERNEL. O hash de senha usa
PBKDF2-HMAC-SHA256 com salt aleatório. A ACL implementa permissões POSIX rwx para
proprietário, grupo e outros, além de entradas específicas por usuário via `Node.acl`.

Arquivos criptografados **não são descriptografados automaticamente**. O comando
`read\\\_file <arquivo> --key CHAVE` exige a chave, valida o autenticador (HMAC) e só
então apresenta o conteúdo em memória; a gravação física permanece cifrada.
`read\\\_file` sem `--key`, ou com chave incorreta, é recusado. `write\\\_file`, `write`
e `append` em arquivos cifrados também exigem `--key`. `decrypt\\\_file <arquivo> --key CHAVE` remove permanentemente a criptografia e grava o texto claro no disco.
A chave não é armazenada nos metadados: são persistidos apenas salt, nonce,
verificador derivado e MAC. A varredura sem a chave verifica os hashes dos blocos,
mas informa que não conseguiu validar o MAC nem procurar assinaturas no texto claro.

**Nota didática:** a cifra em repouso é uma simulação baseada em fluxo derivado de
SHA-256 e autenticação HMAC, não uma implementação criptográfica para produção.
Use uma biblioteca auditada com AES-GCM ou ChaCha20-Poly1305 em sistemas reais.

## Etapa 4: Gerenciamento de Memória

```
python -m so\\\_simulator                                       # RAM: 4 MB reservada + 64 KB dinâmica
python -m so\\\_simulator --ram-dynamic 12KB --replacement lru  # só 3 molduras: page faults fáceis de ver
python -m so\\\_simulator --swap-device ssd --replacement second\\\_chance
python -m unittest discover -s tests -t .
```

Opções: `--ram-reserved 4MB`, `--kernel-base 1MB`, `--ram-dynamic 64KB`, `--page-size 4096`,
`--replacement fifo|lru|second\\\_chance|lfu|nru`, `--reserved-policy first|best|worst`,
`--swap-size 256KB`, `--swap-device hdd|ssd`, `--report-every 10` (0 desliga o relatório periódico).

**Modelo híbrido:** `RAM = \\\[ Região Reservada (contígua, sem paginação) | Região Dinâmica (paginada) ]`.
O Núcleo Base (1 MB) fica fixo no início da área reservada e nunca é movido.

* `memory/reserved.py`: alocação contígua (first/best/worst-fit), lacunas, diagnóstico de fragmentação externa e compactação
* `memory/memory.py`: processos, tabela de páginas, tradução, page fault, swap, relatórios e segurança
* `memory/replacement.py`: FIFO, LRU, Segunda Chance, LFU e NRU (cada um explica a escolha da vítima) + simulador de cadeias de referência
* `memory/swap.py`: memória secundária sobre um dispositivo da Etapa 2
* `memory/comandos.py`: comandos do shell

Comandos: `create\\\_process`, `terminate\\\_process`, `memory\\\_status \\\[--reserved] \\\[--virtual] \\\[--process N]`,
`memory\\\_compact --reserved`, `translate\\\_address`, `execute\\\_step`, `mem\\\_write`, `mem\\\_read`,
`memory\\\_algo`, `memory\\\_policy`, `memory\\\_report \\\[--every N]`, `swap\\\_log`, `page\\\_sim`.

### Exemplo 4.1: área reservada, fragmentação externa e compactação

```
> create\\\_process --name "Kernel\\\_DiskDriver" --size 512KB --type critical
- Alocado de forma contígua na Área Reservada: \\\[1.0 MB até 1.5 MB] (512 KB)
> create\\\_process --name "Kernel\\\_NetDriver" --size 1024KB --type critical
> create\\\_process --name "Kernel\\\_CryptoEngine" --size 512KB --type critical
> terminate\\\_process --name "Kernel\\\_NetDriver"
> memory\\\_status --reserved        # mapa com as 2 lacunas + diagnóstico (maior bloco 1024 KB)
> memory\\\_compact --reserved       # 'Kernel\\\_CryptoEngine' vai de \\\[2.5 MB a 3.0 MB] para \\\[1.5 MB a 2.0 MB]
```

### Exemplo 4.2: memória virtual, fragmentação interna e LRU

```
> create\\\_process --name "App\\\_Navegador" --size 10KB --type normal      # 3 páginas, 2048 Bytes de fragmentação interna
> translate\\\_address --process App\\\_Navegador --page 1 \\\[--offset 100]      # \\\[HIT] ou page fault resolvido
> execute\\\_step --process App\\\_Navegador --instruction "LOAD\\\_DATA Página 2"
\\\[PAGE FAULT DETECTADO]: Página Lógica 2 ausente na RAM!
Memória RAM cheia. Executando Substituição de Página (Algoritmo LRU):
- Analisando histórico de acesso: Página Lógica 0 do PID 100 não é usada há 60ms (Mais antiga).
- Removendo Página Lógica 0 do PID 100 da RAM -> Salvando no Swap (Disco).
```

`execute\\\_step` entende `LOAD/READ` (leitura) e `STORE/WRITE` (escrita, liga o bit M). `page\\\_sim --refs 7,0,1,2,0,3,0,4,2,3,0,3,2,1,2,0,1,7,0,1 --frames 3 --algo all` compara os algoritmos
(FIFO 15, LRU 12, Ótimo 9 page faults); com `1,2,3,4,1,2,5,1,2,3,4,5` aparece a anomalia de Belady no FIFO.

### Integração com as etapas anteriores

* **E/S (Etapa 2):** o swap é um dispositivo da camada de E/S. Todo page-out/page-in gera requisições
escalonadas (`\\\[E/S - SWAP]`); com `--swap-device ssd` aparecem FTL, TRIM e Wear Leveling.
Páginas limpas que já têm cópia no swap são descartadas sem escrita.
* **Segurança (Etapa 3):** processos críticos e `memory\\\_compact` exigem MODO\_KERNEL; um usuário comum
só acessa, traduz, executa e encerra os próprios processos (as negações vão para `security\\\_log`).
* **Processos (Etapa 5):** `MemoryManager.criar\\\_processo/encerrar/referenciar` são a API que o
gerenciador de processos pode chamar; hoje os comandos `create\\\_process`/`terminate\\\_process`/`execute\\\_step`
vivem em `memory/`.

### Simplificações

Alocação por demanda: páginas só ganham moldura no primeiro acesso (zero-fill). Substituição global
(a vítima pode ser de outro processo). O tempo é simulado: 5 ms por referência e +20 ms por page fault.
Dados das páginas são reais (`mem\\\_write`/`mem\\\_read`) e sobrevivem ao swap.

