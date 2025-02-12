#!/bin/bash

# Verifica se o diretório atual é um repositório Git; se não for, inicializa o repositório.
if [ ! -d ".git" ]; then
  echo "Inicializando repositório Git..."
  git init
else
  echo "Repositório Git já existe."
fi

# Adiciona todos os arquivos do projeto para o commit.
echo "Adicionando arquivos..."
git add .

# Se houver alterações não comitadas, realiza o commit.
if ! git diff-index --quiet HEAD --; then
  commit_message=${1:-"Atualiza o projeto com novas alterações"}
  echo "Realizando commit com a mensagem: $commit_message"
  git commit -m "$commit_message"
else
  echo "Nada a commitar, working tree clean"
fi

# Define a URL do repositório no GitHub (usando SSH).
remote_url="git@github.com:DimbaPride/nerai.git"

# Adiciona o repositório remoto se ele não existir.
if ! git remote | grep -q origin; then
  echo "Adicionando repositório remoto..."
  git remote add origin $remote_url
fi

# Realiza o pull para integrar as alterações remotas, usando rebase.
echo "Realizando pull na branch master..."
git pull --rebase origin master

# Envia as alterações para a branch master.
branch="master"
echo "Enviando alterações para o GitHub na branch $branch..."
git push -u origin $branch