(function () {
  function getCookie(name) {
    const cookies = document.cookie ? document.cookie.split(';') : [];
    for (const cookie of cookies) {
      const trimmed = cookie.trim();
      if (trimmed.startsWith(name + '=')) {
        return decodeURIComponent(trimmed.slice(name.length + 1));
      }
    }
    return '';
  }

  // Main initialization
  document.addEventListener('DOMContentLoaded', async function () {
    console.log('🔍 Lab requests script - DOMContentLoaded fired');
    
    const page = document.querySelector('[data-lab-services-url][data-add-lab-service-url]');
    if (!page) {
      console.warn('⚠️ Lab services page wrapper not found');
      return;
    }

    // Get DOM elements
    const select = document.getElementById('lab-service-select');
    const addSelectedButton = document.getElementById('add-selected-service-btn');
    const openModalButton = document.getElementById('open-add-service-modal-btn');
    const selectedList = document.getElementById('selected-services-list');
    const selectedEmpty = document.getElementById('selected-services-empty');
    const hiddenField = document.querySelector('input[name="lab_services"]');
    const modal = document.getElementById('add-service-modal');
    const closeModalButton = document.getElementById('close-modal-btn');
    const addServiceForm = document.getElementById('add-service-form');
    const modalMessage = document.getElementById('modal-message');
    const saveServiceButton = document.getElementById('save-service-btn');

    // Verify critical elements exist
    const requiredElements = { select, modal, addServiceForm, modalMessage };
    const missing = Object.keys(requiredElements).filter(k => !requiredElements[k]);
    if (missing.length > 0) {
      console.error('❌ Missing critical elements:', missing);
      return;
    }
    console.log('✅ All critical elements found');

    // Initialize state
    const servicesById = new Map();
    const selectedServices = new Map();
    let allServices = [];

    // Try to get embedded data
    const availableScript = document.getElementById('available-lab-services-data');
    const selectedIdsScript = document.getElementById('selected-lab-service-ids-data');
    
    if (availableScript) {
      try {
        allServices = JSON.parse(availableScript.textContent);
        console.log('📋 Loaded services from embedded JSON:', allServices.length);
      } catch (e) {
        console.warn('⚠️ Failed to parse embedded services JSON:', e);
      }
    }

    const selectedIds = selectedIdsScript ? JSON.parse(selectedIdsScript.textContent) : [];
    console.log('✅ Selected IDs:', selectedIds.length);

    // Fetch from API if no embedded data
    if (allServices.length === 0) {
      console.log('📡 No embedded services, fetching from API...');
      try {
        const response = await fetch(page.dataset.labServicesUrl);
        if (response.ok) {
          allServices = await response.json();
          console.log('✅ Loaded services from API:', allServices.length);
        } else {
          console.error('API error:', response.status);
        }
      } catch (error) {
        console.error('❌ Failed to fetch from API:', error);
      }
    }

    // Build service maps
    allServices.forEach(service => {
      servicesById.set(Number(service.id), {
        id: Number(service.id),
        name: service.name,
        price: service.price
      });
    });

    selectedIds.forEach(id => {
      const numericId = Number(id);
      if (servicesById.has(numericId)) {
        selectedServices.set(numericId, servicesById.get(numericId));
      }
    });

    console.log('🎯 Initialization complete. Services:', servicesById.size, '| Selected:', selectedServices.size);

    // UI Functions
    function populateSelect() {
      const currentValue = select.value;
      select.innerHTML = '<option value="">Select a lab service</option>';
      
      const available = Array.from(servicesById.values())
        .filter(s => !selectedServices.has(s.id))
        .sort((a, b) => a.name.localeCompare(b.name));
      
      available.forEach(service => {
        const option = document.createElement('option');
        option.value = String(service.id);
        option.textContent = `${service.name} (${service.price})`;
        select.appendChild(option);
      });
      
      if ([...select.options].some(opt => opt.value === currentValue)) {
        select.value = currentValue;
      }
      console.log('✨ Dropdown populated:', available.length, 'options');
    }

    function syncHiddenField() {
      hiddenField.value = Array.from(selectedServices.keys()).join(',');
    }

    function renderSelectedServices() {
      selectedList.innerHTML = '';
      
      if (selectedServices.size === 0) {
        selectedEmpty.classList.remove('hidden');
        syncHiddenField();
        populateSelect();
        return;
      }

      selectedEmpty.classList.add('hidden');
      
      Array.from(selectedServices.values())
        .sort((a, b) => a.name.localeCompare(b.name))
        .forEach(service => {
          const chip = document.createElement('div');
          chip.className = 'inline-flex items-center gap-2 rounded-full bg-blue-100 px-3 py-1.5 text-sm text-blue-900';
          chip.innerHTML = `<span>${service.name} (${service.price})</span>`;

          const removeBtn = document.createElement('button');
          removeBtn.type = 'button';
          removeBtn.className = 'font-bold text-blue-700 hover:text-blue-900 cursor-pointer';
          removeBtn.textContent = '✕';
          removeBtn.addEventListener('click', e => {
            e.preventDefault();
            selectedServices.delete(service.id);
            renderSelectedServices();
          });

          chip.appendChild(removeBtn);
          selectedList.appendChild(chip);
        });
      
      syncHiddenField();
      populateSelect();
    }

    function addSelectedService() {
      const serviceId = Number(select.value);
      if (!serviceId || !servicesById.has(serviceId)) return;
      selectedServices.set(serviceId, servicesById.get(serviceId));
      select.value = '';
      renderSelectedServices();
    }

    function showModalMessage(text, type) {
      modalMessage.textContent = text;
      modalMessage.className = 'rounded-lg p-3 text-sm';
      if (type === 'error') {
        modalMessage.classList.add('bg-red-50', 'text-red-700', 'border', 'border-red-200');
      } else {
        modalMessage.classList.add('bg-green-50', 'text-green-700', 'border', 'border-green-200');
      }
      modalMessage.classList.remove('hidden');
    }

    function openModal() {
      console.log('🔓 Opening modal');
      modal.classList.remove('hidden');
      const nameInput = modal.querySelector('#service-name');
      if (nameInput) nameInput.focus();
    }

    function closeModal() {
      console.log('🔒 Closing modal');
      modal.classList.add('hidden');
      addServiceForm.reset();
      modalMessage.classList.add('hidden');
    }

    // Event Listeners
    addSelectedButton.addEventListener('click', () => {
      console.log('🖱️ Add Selected clicked');
      addSelectedService();
    });

    select.addEventListener('change', () => {
      if (select.value) {
        console.log('🔄 Service selected:', select.value);
        addSelectedService();
      }
    });

    openModalButton.addEventListener('click', openModal);
    closeModalButton.addEventListener('click', closeModal);

    modal.addEventListener('click', e => {
      if (e.target === modal) closeModal();
    });

    addServiceForm.addEventListener('submit', async e => {
      e.preventDefault();
      saveServiceButton.disabled = true;

      const formData = new FormData(addServiceForm);
      try {
        const response = await fetch(page.dataset.addLabServiceUrl, {
          method: 'POST',
          headers: {
            'X-CSRFToken': getCookie('csrftoken'),
            'X-Requested-With': 'XMLHttpRequest'
          },
          body: formData
        });

        const payload = await response.json();
        
        if (!response.ok) {
          showModalMessage(payload.error || 'Failed to create service', 'error');
          return;
        }

        const service = {
          id: Number(payload.id),
          name: payload.name,
          price: payload.price
        };
        
        servicesById.set(service.id, service);
        selectedServices.set(service.id, service);
        renderSelectedServices();
        showModalMessage(payload.message || 'Service added', 'success');
        
        setTimeout(closeModal, 1200);
      } catch (error) {
        console.error('Form submission error:', error);
        showModalMessage('Failed to create service. Please try again.', 'error');
      } finally {
        saveServiceButton.disabled = false;
      }
    });

    // Initial render
    renderSelectedServices();
    console.log('✅ Lab services UI ready!');
  });
})();
