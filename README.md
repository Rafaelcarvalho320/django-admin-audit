# django-admin-audit

[![CI](https://github.com/Rafaelcarvalho320/django-admin-audit/actions/workflows/ci.yml/badge.svg)](https://github.com/Rafaelcarvalho320/django-admin-audit/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue)](https://www.python.org/)
[![Django](https://img.shields.io/badge/django-4.2%20%7C%205.x-092E20)](https://www.djangoproject.com/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Registra **quem mudou o quê** no admin do Django, com diff de antes e depois,
e deixa isso legível no próprio admin. Uma dependência de runtime: o Django.

> *[English summary below](#english-summary)*

## O problema

O `LogEntry` que já vem no admin guarda que alguém salvou algo, e uma mensagem
tipo "Changed title and body". Não guarda os **valores**. Então quando alguém
pergunta "quem mudou esse preço, e de quanto pra quanto?", a resposta é: não dá
para saber.

Uma entrada deste pacote, gerada de verdade por um `POST` no admin:

```json
{
  "body":       { "before": "original body",     "after": "revised body" },
  "price":      { "before": "19.90",             "after": "24.50" },
  "published":  { "before": false,               "after": true },
  "title":      { "before": "A Hora da Estrela", "after": "A Hora da Estrela (2a ed.)" },
  "api_token":  { "before": "[redacted]",        "after": "[redacted]" },
  "tags":       { "before": [],                  "after": [1] }
}
```

Um clique, **uma** entrada: campos próprios, decimal exato, booleano, m2m — e o
token aparece como rotacionado sem que nenhum dos dois valores exista no banco.

## Instalação

```bash
pip install git+https://github.com/Rafaelcarvalho320/django-admin-audit.git
```

```python
# settings.py
INSTALLED_APPS = [
    ...,
    "django.contrib.contenttypes",
    "admin_audit",
]
```

```python
# qualquer admin.py
from admin_audit import AuditedModelAdmin


@admin.register(Article)
class ArticleAdmin(AuditedModelAdmin):
    list_display = ["title", "author", "published"]
```

```bash
python manage.py migrate
```

Pronto. As entradas aparecem em `/admin/admin_audit/auditentry/`, filtráveis por
ação, tipo de objeto e data, com o diff renderizado como tabela.

## Decisões que não são óbvias

**Segredo nunca entra na tabela.** Um log de auditoria acumula para sempre, é
lido por mais gente que o registro original, e vai parar em backup e em dump de
staging. Hash de senha, token de API e número de cartão são barrados no único
ponto onde um valor se torna entrada de log — não "redigidos depois", nunca
escritos. A lista casa por substring (`password`, `token`, `secret`, `cvv`,
`card_number`, …) e um campo pode marcar `audit_sensitive = True`.

**Mas a rotação continua auditável.** Os dois lados de um campo sensível
redigem para o mesmo marcador, então um diff comum reportaria um token trocado
como "nenhuma mudança" e perderia o fato. A comparação é feita em memória
contra os valores reais, e o que se grava é
`{"before": "[redacted]", "after": "[redacted]"}`. Um hash faria o diff
funcionar sem esse trabalho, e é exatamente o que não pode ser guardado: um
segredo fraco cai num ataque offline contra ele.

**Quatro hooks, não um.** O admin escreve um objeto em mais de uma etapa:

| Hook | O que captura |
| --- | --- |
| `save_model` | Os campos próprios. O "antes" vem de uma releitura do banco |
| `save_related` | m2m e inlines, que são escritos **depois** do `save_model` |
| `delete_model` | Uma exclusão |
| `delete_queryset` | A ação em massa, que **nunca** chama `delete_model` |

Um mixin que sobrescreve só `save_model` perde todo m2m e todas as exclusões
feitas pelo dropdown da listagem.

**Uma ação, uma entrada.** m2m é escrito num hook posterior, então o caminho
ingênuo grava duas linhas para um clique: uma do objeto e outra das relações.
Aqui o `save_related` **mescla** na entrada que o `save_model` acabou de
escrever, e só cria uma nova quando nada além das relações mudou.

**Salvar sem mudar nada não registra nada.** O admin salva o form de qualquer
jeito, e um log cheio de "atualizado, zero campos" é um log que as pessoas
param de ler.

**`SET_NULL` no usuário, com o nome ao lado.** Apagar um usuário não pode
apagar o registro do que ele fez — é a única coisa que um log de auditoria
existe para impedir. O `username` fica em texto na própria linha, então a
entrada continua dizendo quem foi depois da conta sumir.

**`id` e `auto_now` ficam fora.** A chave surrogate nunca muda e um
`auto_now` muda em todo save, o que faria toda entrada parecer uma modificação.

**Falhar ao registrar não desfaz a mudança.** A escrita é best-effort: a
exceção vai para o log e é engolida. Um admin que para de funcionar porque a
tabela de auditoria encheu é pior que um que salva e reclama.

**`Decimal` vira string, nunca float.** Float mudaria o número no caminho, o
que num log de auditoria é pior que não guardar.

**Texto longo é truncado em 500 caracteres.** A linha registra o que mudou; não
é uma segunda cópia do banco. Sem isso, um campo de corpo de 2 MB em cada
entrada faz do log a maior tabela do schema.

## O modelo

```text
class AuditEntry(models.Model):
    created_at    # indexado
    action        # create | update | delete
    user          # FK, SET_NULL
    username      # texto, sobrevive ao usuário
    content_type  # + object_id + object_repr
    changes       # JSONField: {campo: {before, after}}
    request_ip
```

`object_id` é texto, então serve para pk inteira, UUID ou chave natural. O
índice composto `(content_type, object_id, -created_at)` é a consulta que toda
investigação roda: o histórico de um objeto, mais recente primeiro.

O admin do `AuditEntry` é somente leitura, inclusive para exclusão. Um log que
alguém consegue editar não responde nenhuma pergunta que se faça a ele; cortar
histórico é uma necessidade real e pertence a um job de retenção com política
escrita, não a um checkbox ao lado das linhas que ele apagaria.

## Importação tardia

`__init__.py` não importa modelo nenhum. Um app Django reutilizável que toca
models no momento do import levanta `AppRegistryNotReady` assim que algo o
importa antes do `django.setup()` — e o `makemigrations` faz isso. O hook do
PEP 562 dá o `from admin_audit import AuditedModelAdmin` sem carregar nada até
o nome ser pedido de fato.

Pelo mesmo motivo, as classes do Django não são subscritas em runtime
(`ModelAdmin[AuditEntry]`): isso depende do `monkeypatch()` do django-stubs-ext,
e uma biblioteca não pode supor que o projeto que a instala chama aquilo. Os
genéricos existem só sob `TYPE_CHECKING`, e o django-stubs-ext saiu das
dependências de runtime por consequência.

## Testes

```bash
pip install -e ".[dev]"
pytest                  # 69 testes
ruff check . && ruff format --check .
mypy                    # strict, com django-stubs
```

Os testes dirigem o admin por **requisições reais**, não chamando `save_model`
na mão. Chamar o hook direto testaria o mixin contra um grafo de objetos que o
admin nunca produz; postar o form exercita a sequência de verdade — e é nela
que os bugs interessantes moram.

```
src/admin_audit/
    serialize.py   valores para JSON, e a recusa de gravar segredo
    diff.py        snapshot, diff e a comparação de campos sensíveis
    mixins.py      os quatro hooks do admin
    models.py      a tabela, append-only
    admin.py       leitura do log, com o diff renderizado
tests/             69 testes, via requisições reais ao admin
```

## English summary

Records **who changed what** in the Django admin, with a before/after diff, and
makes it readable in the admin itself. One runtime dependency: Django.

Django's own `LogEntry` records that something was saved and a message like
"Changed title and body". It does not record the values, so "who changed this
price, and from what?" has no answer.

Decisions worth knowing:

- **Secrets never enter the table.** An audit log accumulates forever, is read
  by more people than the original record, and ends up in backups and staging
  dumps. Redaction happens at the only point where a value becomes a log entry.
- **Rotation stays auditable anyway.** Both sides of a redacted field reduce to
  the same marker, so an ordinary diff would report a rotated token as no
  change and lose the fact. The comparison runs in memory against the real
  values and stores only the markers. A hash would make the diff work for free
  and is precisely what must not be stored.
- **Four admin hooks, not one.** `save_model` for the object's own fields,
  `save_related` for m2m and inlines (written later, so diffing them at save
  time reports every change as none), `delete_model`, and `delete_queryset` —
  the bulk action never calls `delete_model`, and missing it is how a log loses
  everything removed from a changelist dropdown.
- **One user action, one entry.** `save_related` merges into the row
  `save_model` just wrote instead of appending a second "update" nobody made.
- **`SET_NULL` on the user, with the username kept as text**, so deleting an
  account cannot delete the record of what it did.
- **Failing to log never undoes the change.** An admin that stops working
  because the audit table filled up is worse than one that saves and complains.
- **Nothing is imported eagerly**, and Django's generics are only subscripted
  under `TYPE_CHECKING`, because a reusable app cannot assume the installing
  project calls `django_stubs_ext.monkeypatch()`.

Code and docstrings are in English; the sections above are in Portuguese.

## Licença

MIT. Veja [LICENSE](LICENSE).
