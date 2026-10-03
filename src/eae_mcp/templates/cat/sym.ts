/*
 * Created by EcoStruxure Automation Expert.
 * User:    
 * Date: @@DATE@@
 * Time: @@TIME@@
 * 
 */
namespace @@NS@@.Symbols.@@CAT@@ {

  export class @@SYM@@ extends NxtControl.GuiFramework.RuntimeSymbol {

    /**
     * Type of an object (never change this)
     * @type String
     * @default
     */
    @System.DefaultValue('@@NS@@.Symbols.@@CAT@@.@@SYM@@')
    protected type: string;
        
	/**** DO NOT DELETE CONSTRUCTOR *****/
    constructor() {
      // do not delete next line
      super();
    }

    /* 
	 * FOLLOWING METHOD ARE NEEDED ONLY IF YOU WANT TO ADD PROPERTIES AND WRITE THE LOGIC
	 *
	 * 1. load is called to set the state when symbol is loaded
	 * 2. _set is meant for setting properties
	 * 3. toObject is used by save to return the properties that are serialized
   
     * @param {Object} [options] Options object
     * @return {Object} thisArg
     *
    load(options:any): this {
      // do not delete next 2 lines
      options = options || { };
      super.load(options);
       
      //if you need to find your shape, use this method as in the sample
      //this.shape = this.find('shapeName');
      
      //if you have callback method to event bind it to this otherwise it won't work
      //this._onMouseDown = this._onMouseDown.bind(this);
       return this;
    }

    // THESE ARE EXAMPLES HOW TO USE PROPERTIES
    //@System.DefaultValue(true)
    //protected propertyName1: boolean;
    //
    //getPropertyName1() {
    // return this.propertyName1;
    //}
    //setPropertyName1(value: boolean) {
    // this._set('propertyName1', value, true); 
    //}
    //@System.DefaultValue(1)
    //local variable that is not saved
    //protected shape: Shape;

 
    protected _set(key: string, value: any, invalidate: boolean = false) : this {
      if (key == 'propertyName1') {
        this[key] = value;
        invalidate && this.invalidate();
        return this;
      }
      // do not delete next line
      super._set(key, value, invalidate);
      return this;
    }

     *
     * Returns object representation of an instance
     * @param {Array} propertiesToInclude
     * @return {Object} object representation of an instance
     *
    toObject(propertiesToInclude?: any[]): any {
      return super.toObject(propertiesToInclude);
      // if you have properties to save comment previous line and uncomment following section
      //  var object = NxtControl.Util.ObjectUtil.extend(this.super.toObject(propertiesToInclude), {
      //    propertyName1: this.propertyName1,
      //    propertyName2: this.propertyName2,
      //  });
      //  if (!this.includeDefaultValues)
      //    this._removeDefaultValues(object);
      //  return object;
    }
	*/
 
   
    /* 
	 * FOLLOWING METHODS ARE NEEDED ONLY IF YOU WANT TO CODE EVENTS
	 * 1. initEvents and disposeEvents 
	 * 2. _onMouseDown is only example
	 * NORMALLY NOT NEEDED, DEFINE EVENT HANDLERS THROUGH PROPERTY GRID
	 * 
	 * subscribe and unsubscribe the events    
    initEvents() {
      // do not delete next line
      super.initEvents();
      //this.shape.mousedown.add(this._onMouseDown);
    }
    
    disposeEvents() {
      // do not delete next line
      this.callSuper('disposeEvents');
      //this.shape.mousedown.remove(this._onMouseDown);
    }
    
    protected _onMouseDown(sender: any, ea: ISystemEventArgs) {
    }
    */
	
    /**
	 * FOLLOWING METHOD IS NEEDED ONLY IF YOU WANT TO SET SOME PROPERTIES FOR FACEPLATE
     * Called when a faceplate is created
     * @param {string} faceplateName The name of the created faceplate
     * @param {Faceplate} faceplate The created faceplate
     */
    //onFaceplateCreated(faceplateName: string, faceplate: Faceplate) {
    //} 
	
  } 
}
