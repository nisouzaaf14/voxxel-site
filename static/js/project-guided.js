(() => {
  const form = document.getElementById('guided-project-form');
  if (!form) return;
  const photos = form.querySelector('#imagens_referencia');
  const model = form.querySelector('#modelo_3d');
  const feedback = form.querySelector('.guided-file-feedback');
  function validate() {
    let error = '';
    if (photos.files.length > 6) error = 'Selecione no máximo 6 imagens.';
    if (model.files[0] && model.files[0].size > 15 * 1024 * 1024) error = 'O modelo deve ter até 15 MB.';
    photos.setCustomValidity(error);
    feedback.textContent = error;
    return !error;
  }
  [photos, model].forEach(input => input.addEventListener('change', () => {
    input.closest('.project-upload-card').classList.toggle('has-files', input.files.length > 0);
    const target = input === model ? 'model-file-name' : 'image-file-name';
    document.getElementById(target).textContent = input.files.length ? (input === model ? input.files[0].name : input.files.length + ' imagem(ns) selecionada(s)') : (input === model ? 'Selecionar modelo' : 'Selecionar imagens');
    photos.setCustomValidity('');
    validate();
  }));
  form.addEventListener('submit', event => {
    const required = form.elements.origem.value !== 'empresas';
    if (!validate() || (required && !photos.files.length && !model.files.length)) {
      event.preventDefault();
      feedback.textContent = feedback.textContent || 'Envie uma foto ou um arquivo 3D para continuar.';
      photos.focus();
    }
  });
  form.addEventListener('invalid', event => {
    const details = event.target.closest('details');
    if (details) details.open = true;
  }, true);
})();
